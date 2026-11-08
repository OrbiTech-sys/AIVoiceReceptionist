"""Turn a business's website or a PDF into knowledge entries the receptionist can answer from.

Text is cut into short passages, each stored with its heading as the "question". Nothing is summarised or invented:
the receptionist later says the best sentences of a saved passage, so it can only repeat what the business published.

Fetching a web address that a user typed is a server-side request, so it is locked down: only http(s) on ports 80 and
443, every address the name resolves to must be public (no localhost, private, link-local or metadata addresses),
each redirect is checked again, and the page size and time are capped.
"""
from __future__ import annotations

import io
import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from ..errors import AppError

MAX_PAGE_BYTES = 1_500_000
MAX_PAGES = 6  # the start page plus a few same-site pages that look useful (services, FAQ, hours, ...)
MAX_ENTRIES = 120
MAX_PDF_PAGES = 40
CHUNK_CHARS = 480
USEFUL_PATHS = re.compile(r"about|service|treatment|faq|question|hour|contact|price|pricing|cost|insurance|location|"
                          r"direction|polic|new-patient|patients|visit|book|team", re.I)
SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "select", "button"}
BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "div", "br", "tr", "td", "th", "section", "article", "dt",
              "dd", "blockquote", "address", "footer", "header", "ul", "ol", "table"}
Block = tuple[str, str]  # (heading, text)


# ------------------------------------------------------------------ fetching safely
def _public_address(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def check_url(url: str) -> str:
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise AppError("Enter a web address starting with http:// or https://", code="invalid_url")
    if p.port not in (None, 80, 443) or p.username or p.password:
        raise AppError("That web address isn't allowed.", code="invalid_url")
    if not _public_address(p.hostname):
        raise AppError("That address can't be reached from here, or it isn't a public website.", code="invalid_url")
    return p.geturl()


def fetch_page(url: str, client: httpx.Client | None = None) -> tuple[str, str]:
    """Returns (final address, page text/HTML). Redirects are followed by hand so each hop is checked."""
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=False,
                                    headers={"User-Agent": "OrbiTechReceptionistBot/1.0 (+business knowledge import)"})
    try:
        for _ in range(4):
            url = check_url(url)
            with client.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    raise AppError(f"The website answered with an error ({r.status_code}).", code="fetch_failed")
                kind = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind not in ("text/html", "application/xhtml+xml", "text/plain"):
                    raise AppError("That address isn't a web page. Upload files such as PDFs with the file button.", code="fetch_failed")
                raw = b""
                for chunk in r.iter_bytes():
                    raw += chunk
                    if len(raw) > MAX_PAGE_BYTES:
                        raise AppError("That page is too large to import.", code="file_too_large")
                return url, raw.decode(r.encoding or "utf-8", errors="replace")
        raise AppError("The website redirected too many times.", code="fetch_failed")
    except httpx.HTTPError:
        raise AppError("Couldn't reach that website. Check the address and try again.", code="fetch_failed")
    finally:
        if own:
            client.close()


# ------------------------------------------------------------------ HTML and PDF to blocks
class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.blocks: list[Block] = []
        self.links: list[str] = []
        self._skip = 0
        self._heading = ""
        self._text: list[str] = []
        self._in_title = False
        self._in_heading = False

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", " ".join(self._text)).strip()
        self._text = []
        if not text:
            return
        if self._in_heading:
            self._heading = text[:120]
        else:
            self.blocks.append((self._heading, text))

    def handle_starttag(self, tag, attrs):
        if tag == "a" and dict(attrs).get("href"):
            self.links.append(dict(attrs)["href"])  # menu links count too: that's where most useful pages are linked
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        if tag in BLOCK_TAGS and not self._skip:
            self._flush()
            self._in_heading = tag in ("h1", "h2", "h3")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS and not self._skip:
            self._flush()
            self._in_heading = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self._text.append(data)


def html_to_blocks(html: str) -> tuple[str, list[Block], list[str]]:
    p = _Extractor()
    p.feed(html)
    p._flush()
    return re.sub(r"\s+", " ", p.title).strip(), p.blocks, p.links


def pdf_to_blocks(data: bytes) -> list[Block]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise AppError("That PDF is password protected.", code="invalid_file")
        pages = [(page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES]]
    except AppError:
        raise
    except Exception:
        raise AppError("That PDF couldn't be read.", code="invalid_file")
    blocks: list[Block] = []
    heading = ""
    for page in pages:
        para: list[str] = []
        lines = [ln.strip() for ln in page.splitlines()]
        for i, line in enumerate(lines + [""]):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            # A short line ending in "?" is a question the document answers underneath: always a new section, even
            # with no blank line before it (PDF text usually has none). Other headings are short lines without
            # closing punctuation.
            is_question = bool(line) and line.endswith("?") and len(line) <= 90 and bool(nxt)
            is_heading = is_question or (bool(line) and len(line) <= 60 and not re.search(r"[.,;:!?]$", line) and bool(nxt) and len(line.split()) <= 8)
            if is_question and para:
                blocks.append((heading, re.sub(r"\s+", " ", " ".join(para)).strip()))
                para = []
            if is_heading and not para:
                heading = line
            elif line:
                para.append(line)
            if (not line or i == len(lines)) and para:
                blocks.append((heading, re.sub(r"\s+", " ", " ".join(para)).strip()))
                para = []
    return blocks


# ------------------------------------------------------------------ blocks to passages
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def blocks_to_entries(blocks: list[Block], title: str = "") -> list[tuple[str, str]]:
    """Group text under its heading and cut it into passages of about CHUNK_CHARS, at sentence ends."""
    grouped: list[tuple[str, str]] = []
    for heading, text in blocks:
        if grouped and grouped[-1][0] == heading:
            grouped[-1] = (heading, f"{grouped[-1][1]} {text}")
        else:
            grouped.append((heading, text))
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for heading, text in grouped:
        cur = ""
        parts: list[str] = []
        for sentence in _SENTENCE.split(text):
            if cur and len(cur) + len(sentence) + 1 > CHUNK_CHARS:
                parts.append(cur)
                cur = sentence
            else:
                cur = f"{cur} {sentence}".strip()
        if cur:
            parts.append(cur)
        for part in parts:
            part = part[:1000]
            if len(part) < 30 or part.lower() in seen:
                continue
            seen.add(part.lower())
            question = heading or title or " ".join(part.split()[:8])
            out.append((question[:300], part))
    return out[:MAX_ENTRIES]


def import_website(url: str, client: httpx.Client | None = None) -> tuple[list[tuple[str, str]], int]:
    """(entries, pages read). Reads the start page and up to a few same-site pages that look useful."""
    start, html = fetch_page(url, client)
    title, blocks, links = html_to_blocks(html)
    host = urlparse(start).hostname
    seen = {start.rstrip("/")}
    pages = 1
    for href in links:
        if pages >= MAX_PAGES:
            break
        link = urljoin(start, href).split("#")[0]
        p = urlparse(link)
        if p.hostname != host or link.rstrip("/") in seen or not USEFUL_PATHS.search(p.path):
            continue
        seen.add(link.rstrip("/"))
        try:
            final, page_html = fetch_page(link, client)
        except AppError:
            continue
        t, b, _ = html_to_blocks(page_html)
        blocks += [(h or t, text) for h, text in b]
        pages += 1
    entries = blocks_to_entries(blocks, title)
    if not entries:
        raise AppError("No readable text was found on that page. Pages that need JavaScript to show their text can't be read; "
                       "try the page's print/PDF version.", code="empty_import")
    return entries, pages


def import_pdf(data: bytes) -> list[tuple[str, str]]:
    entries = blocks_to_entries(pdf_to_blocks(data))
    if not entries:
        raise AppError("No text was found in that PDF (scanned pages are images and can't be read).", code="empty_import")
    return entries

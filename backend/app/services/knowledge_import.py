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


# ------------------------------------------------------------------ what may be said to a caller
_PLACEHOLDER = re.compile(r"\[[^\]]{2,}\]")  # a template field nobody filled in: "[Enter clinic name]"
_PAGE_FURNITURE = re.compile(r"customizable template|customize and verify|verify before use|\bpage \d+\b|all rights reserved", re.I)
_STAFF_VERB = re.compile(r"^(ask|confirm|verify|notify|document|record|escalate|transfer|advise|repeat|greet|log|collect|route|refer|obtain|"
                         r"inform|ensure|identify|determine|clarify|offer to|understand:|empathi[sz]e|"
                         r"do not (promise|disclose|share|diagnose|give|quote|discuss|speculate|guarantee|argue|interrupt))\b", re.I)
_ABOUT_STAFF = re.compile(r"\b(the caller|callers?'s?|the receptionist|receptionists?|front desk|staff member|escalat\w+|script|protocol)\b", re.I)
_BULLETS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u2022\u25cf\u25aa\u25a0\u25e6\u25ba\u2023\u2043\uf0b7\uf0a7]+")
_SENT = re.compile(r"(?<=[.!?])\s+")


def clean_text(text: str) -> str:
    """Bullet symbols and control characters that PDFs carry as text, and blank-looking runs of space."""
    return re.sub(r"\s+", " ", _BULLETS.sub(" ", text)).strip()


def scrub_answer(answer: str) -> str:
    """Cut out sentences that can't be said to a caller (staff instructions, template fields, page furniture), so one glued
    onto the end of a real answer doesn't spoil it."""
    keep = [x for x in (y.strip() for y in _SENT.split(clean_text(answer)))
            if x and not (_STAFF_VERB.match(x) or _ABOUT_STAFF.search(x) or _PLACEHOLDER.search(x) or _PAGE_FURNITURE.search(x))]
    return " ".join(keep)


def is_speakable(question: str, answer: str) -> bool:
    """Could this be said to a caller as it stands? Not if it has template fields still to be filled in, is page header or
    footer text, or is an instruction written for the receptionist ("Ask for the patient's name") rather than a fact."""
    text = f"{question} {answer}"
    if _PLACEHOLDER.search(text) or _PAGE_FURNITURE.search(text) or _BULLETS.search(text):
        return False
    sentences = [x.strip() for x in _SENT.split(answer) if x.strip()]
    if not sentences:
        return False
    staff = sum(bool(_STAFF_VERB.match(x) or _ABOUT_STAFF.search(x)) for x in sentences)
    return staff * 2 < len(sentences) or (len(sentences) == 1 and not staff)


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
    # Lines that repeat on several pages (running headers and footers, "Page 3") are not content.
    page_lines = [[clean_text(ln) for ln in page.splitlines()] for page in pages]
    seen: dict[str, int] = {}
    for lines in page_lines:
        for key in {re.sub(r"\d+", "#", ln) for ln in lines if ln and len(ln) <= 140}:
            seen[key] = seen.get(key, 0) + 1
    repeat_at = max(2, int(len(pages) * 0.4 + 0.99))
    blocks: list[Block] = []
    heading = ""
    for lines in page_lines:
        lines = [ln for ln in lines if not (ln and (re.fullmatch(r"(page )?\d+( of \d+)?", ln, re.I) or
                                                    (len(ln) <= 140 and seen.get(re.sub(r"\d+", "#", ln), 0) >= repeat_at)))]
        lines = [re.sub(r"^\d+[.)]\s+(?=\S)", "", ln) for ln in lines]  # "3. Insurance" -> "Insurance"
        para: list[str] = []
        for i, line in enumerate(lines + [""]):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            # A short line ending in "?" is a question the document answers underneath: always a new section, even
            # with no blank line before it (PDF text usually has none). Other headings are short lines without
            # closing punctuation.
            is_question = bool(line) and line.endswith("?") and len(line) <= 90 and bool(nxt) and not line.startswith("Q:")
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
    entries = [(clean_text(q), clean_text(a)) for q, a in blocks_to_entries(blocks, title)]
    entries = [(q, a) for q, a in entries if is_speakable(q, a)]
    if not entries:
        raise AppError("No readable text was found on that page. Pages that need JavaScript to show their text can't be read; "
                       "try the page's print/PDF version.", code="empty_import")
    return entries, pages


_QA = re.compile(r"\bQ:\s*(.+?)\s*\bA:\s*(.+?)(?=\s*\bQ:\s|\Z)", re.S)


def import_pdf(data: bytes) -> tuple[list[tuple[str, str]], int]:
    """(entries, how many passages were left out because they couldn't be said to a caller)."""
    blocks, pairs = [], []
    for heading, text in pdf_to_blocks(data):
        found = _QA.findall(text)
        if found:  # "Q: ... A: ..." written out in the document
            pairs += [(clean_text(q)[:300], clean_text(a)[:1000]) for q, a in found]
            text = _QA.split(text)[0].strip()
        if text:
            blocks.append((heading, text))
    entries = pairs + blocks_to_entries(blocks)
    kept = [(q, scrub_answer(a)) for q, a in entries if is_speakable(q, scrub_answer(a)) or is_speakable(q, a)]
    kept = [(q, a) for q, a in kept if a and is_speakable(q, a)]
    return finish_import(kept, len(entries) - len(kept))


def finish_import(entries: list[tuple[str, str]], ignored: int) -> tuple[list[tuple[str, str]], int]:
    entries = list(dict.fromkeys(entries))[:MAX_ENTRIES]
    if not entries:
        raise AppError("Nothing in that PDF can be said to callers as written: it has no readable text (scanned pages are images), "
                       "or it is made of template fields to fill in and instructions for staff. Fill it in and write answers "
                       "as facts, or use a question-and-answer list (CSV).", code="empty_import")
    return entries, ignored

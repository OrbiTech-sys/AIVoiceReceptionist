"""Reading a business's website or a PDF into knowledge the receptionist can answer from."""
from __future__ import annotations

import httpx
import pytest
from test_conversation import _second_call

from app.errors import AppError
from app.services import knowledge_import as ki
from app.voice import nlu
from app.voice.dialog import Receptionist
from app.voice.tools import TenantSnapshot

HOME = """<html><head><title>Bright Smile Dental</title><style>.x{}</style><script>var secret = 1;</script></head>
<body><nav><a href="/services">Services</a><a href="/faq">FAQ</a><a href="https://other.example/x">Elsewhere</a><a href="/blog/post-1">Blog</a></nav>
<h1>Welcome</h1><p>Bright Smile Dental has cared for families in Austin since 2004. We are a friendly, modern practice.</p>
<h2>Insurance</h2><p>We accept Delta Dental, Cigna and Aetna. We also offer payment plans for treatments over $500. Ask our team about financing.</p>
<footer><p>Open Monday to Friday, 9 to 5. Call 555-0100.</p></footer></body></html>"""
FAQ = """<html><title>FAQ</title><body><h2>Parking</h2><p>Free parking is available behind the building on Oak Street. Enter from the alley.</p></body></html>"""
SERVICES = "<html><title>Services</title><body><h2>Teeth whitening</h2><p>Professional whitening takes about an hour and lasts up to two years.</p></body></html>"


def _site(monkeypatch, pages: dict[str, str]):
    """Serve fake pages; every address counts as public."""
    monkeypatch.setattr(ki, "_public_address", lambda host: True)

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_a_website_becomes_short_passages_under_their_headings(monkeypatch):
    client = _site(monkeypatch, {"/": HOME, "/services": SERVICES, "/faq": FAQ})
    entries, pages = ki.import_website("https://brightsmile.example/", client)
    assert pages == 3  # the home page plus the two useful pages; the blog and the other site were left alone
    by_heading = {q: a for q, a in entries}
    assert "Delta Dental, Cigna and Aetna" in by_heading["Insurance"]
    assert "behind the building on Oak Street" in by_heading["Parking"]
    assert "Teeth whitening" in by_heading
    assert not any("secret" in a or "var " in a for _, a in entries)  # scripts and styles never become knowledge
    assert any("Open Monday to Friday" in a for _, a in entries)  # footers (hours, phone) are kept


@pytest.mark.parametrize("url", [
    "http://localhost/", "http://127.0.0.1/", "http://10.0.0.5/admin", "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/", "ftp://example.com/", "file:///etc/passwd", "https://example.com:8443/", "http://user:pw@example.com/",
])
def test_internal_and_odd_addresses_are_refused(url):
    with pytest.raises(AppError) as e:
        ki.check_url(url)
    assert e.value.code == "invalid_url"


def test_a_redirect_to_an_internal_address_is_refused(monkeypatch):
    monkeypatch.setattr(ki, "_public_address", lambda host: host == "public.example")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(302, headers={"location": "http://internal.example/secret"})), follow_redirects=False)
    with pytest.raises(AppError) as e:
        ki.fetch_page("https://public.example/", client)
    assert e.value.code == "invalid_url"


def test_big_or_non_page_responses_are_refused(monkeypatch):
    monkeypatch.setattr(ki, "_public_address", lambda host: True)
    big = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, content=b"a" * (ki.MAX_PAGE_BYTES + 10), headers={"content-type": "text/html"})))
    with pytest.raises(AppError) as e:
        ki.fetch_page("https://x.example/", big)
    assert e.value.code == "file_too_large"
    pdf = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})))
    with pytest.raises(AppError):
        ki.fetch_page("https://x.example/", pdf)


def test_a_page_without_text_says_why(monkeypatch):
    client = _site(monkeypatch, {"/": "<html><body><div id='app'></div><script>render()</script></body></html>"})
    with pytest.raises(AppError) as e:
        ki.import_website("https://spa.example/", client)
    assert e.value.code == "empty_import" and "JavaScript" in e.value.message


# ------------------------------------------------------------------ PDF
def _pdf(text_lines: list[str]) -> bytes:
    """A minimal one-page PDF with the given lines of text."""
    content = "BT /F1 12 Tf 14 TL 50 700 Td " + " ".join(f"({ln}) Tj T*" for ln in text_lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()


def test_a_pdf_becomes_passages():
    data = _pdf(["New Patient Guide", "Please arrive ten minutes early and bring your insurance card.", "We take Delta Dental and Cigna."])
    entries, _ = ki.import_pdf(data)
    assert any("insurance card" in a and "Delta Dental" in a for _, a in entries)


def test_unreadable_pdfs_are_refused_politely():
    with pytest.raises(AppError):
        ki.import_pdf(b"%PDF-1.4 not really")


# ------------------------------------------------------------------ through the API, then on the phone
def test_api_imports_a_pdf_and_a_website_without_duplicates(owner, monkeypatch):
    pdf = _pdf(["Policies", "Cancellations need 24 hours notice. Late cancellations may be charged a fee."])
    r = owner.post("/api/knowledge/import", files={"file": ("guide.pdf", pdf, "application/pdf")})
    assert r.status_code == 200 and r.json()["imported"] >= 1
    again = owner.post("/api/knowledge/import", files={"file": ("guide.pdf", pdf, "application/pdf")})
    assert again.json()["imported"] == 0 and again.json()["skipped"] >= 1
    assert owner.post("/api/knowledge/import", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 400

    client = _site(monkeypatch, {"/": HOME})
    monkeypatch.setattr(ki, "fetch_page", lambda url, c=None, _f=ki.fetch_page: _f(url, client))
    site = owner.post("/api/knowledge/import-url", json={"url": "https://brightsmile.example/"})
    assert site.status_code == 200 and site.json()["imported"] >= 2 and site.json()["pages"] == 1
    kinds = {k["kind"] for k in owner.get("/api/knowledge").json()}
    assert "general" in kinds


def test_api_refuses_internal_addresses(owner):
    for url in ("http://127.0.0.1/", "http://localhost/admin", "http://169.254.169.254/latest/meta-data/", "ftp://example.com/x"):
        assert owner.post("/api/knowledge/import-url", json={"url": url}).status_code == 400


def _knows(entries: list[tuple[str, str]]) -> Receptionist:
    r, _ = _second_call()
    kb = [{"id": f"p{i}", "kind": "general", "q": q, "a": a} for i, (q, a) in enumerate(entries)]
    snap = r.snap.__class__(**{**r.snap.__dict__, "kb": kb})
    return Receptionist(snap, r.tools, now=r.now)


def test_the_receptionist_answers_from_the_saved_passages_in_a_sentence_or_two(monkeypatch):
    client = _site(monkeypatch, {"/": HOME, "/faq": FAQ, "/services": SERVICES})
    entries, _ = ki.import_website("https://brightsmile.example/", client)
    r = _knows(entries)
    reply = r.handle("Do you accept Delta Dental insurance?").text
    assert reply.startswith("We accept Delta Dental, Cigna and Aetna.") and "Is there anything else" in reply
    assert "Austin" not in reply  # only what answers the question
    assert "Oak Street" in r.handle("Where can I park?").text
    assert "an hour" in r.handle("How long does teeth whitening take?").text
    assert "Austin since 2004" not in r.handle("What is the weather today?").text  # unrelated: not answered from the site


def test_long_passages_are_cut_to_the_sentences_that_answer():
    long_answer = ("We have been open since 1990. " * 6) + "Cancellations need 24 hours notice. Late ones may be charged a fee. " + ("Our team is friendly. " * 6)
    said = nlu.best_sentences(long_answer, "What is the cancellation notice?")
    assert said.startswith("Cancellations need 24 hours notice.") and len(said) <= 300
    assert nlu.best_sentences("Short answer.", "anything") == "Short answer."

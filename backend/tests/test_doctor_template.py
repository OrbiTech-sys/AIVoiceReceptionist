"""The ready-made doctor's-office knowledge base imports, and the receptionist answers from it safely."""
from __future__ import annotations

from pathlib import Path

import pytest
from test_conversation import _second_call

from app.api.catalog import parse_knowledge_file
from app.voice.dialog import Receptionist

FILE = Path(__file__).resolve().parent.parent.parent / "knowledge-templates" / "doctor-office.csv"


@pytest.fixture(scope="module")
def pairs():
    return parse_knowledge_file(FILE.name, FILE.read_bytes())


def test_the_template_imports_and_fits_the_limits(pairs):
    assert len(pairs) >= 30
    assert all(2 <= len(q) <= 300 and 1 <= len(a) <= 2000 for q, a in pairs)
    assert len({q for q, _ in pairs}) == len(pairs)  # no duplicate questions


def test_it_promises_nothing_only_the_practice_knows(pairs):
    text = " ".join(a.lower() for _, a in pairs)
    # no invented prices, hours, phone numbers or plan names, and no medical advice
    assert "$" not in text and not any(ch.isdigit() for ch in text.replace("911", "").replace("988", "").replace("15 minutes", ""))
    for banned in ("medicare", "medicaid", "blue cross", "aetna", "cigna", "take two", "you should take", "dose"):
        assert banned not in text


def _clinic(pairs) -> Receptionist:
    r, tools = _second_call()
    kb = [{"id": f"k{i}", "kind": "faq", "q": q, "a": a} for i, (q, a) in enumerate(pairs)]
    snap = r.snap.__class__(**{**r.snap.__dict__, "kb": kb, "business_type": "medical", "name": "Riverside Family Clinic"})
    return Receptionist(snap, tools, now=r.now)


@pytest.mark.parametrize("asked, expected", [
    ("What should I bring to my first visit?", "photo ID"),
    ("Do you accept Blue Cross insurance?", "can't confirm a specific plan"),
    ("How do I get a prescription refill?", "pharmacy"),
    ("How can I get my test results?", "not able to read them"),
    ("Do I need to fast before my blood test?", "Fasting instructions"),
    ("Can I get a copy of my medical records?", "release form"),
    ("I have a question about my bill", "billing team"),
    ("Is my information private?", "confidential"),
    ("Do you give flu shots?", "vaccines"),
])
def test_common_questions_get_the_saved_answer(pairs, asked, expected):
    reply = _clinic(pairs).handle(asked).text
    assert expected in reply, (asked, reply)


@pytest.mark.parametrize("said", ["I have severe chest pain", "I want to kill myself", "I can't breathe"])
def test_emergencies_are_never_left_to_the_knowledge_base(pairs, said):
    r = _clinic(pairs)
    reply = r.handle(said).text
    assert "urgent" in " ".join(r.outcome_flags) or "emergency" in reply.lower() or "911" in reply
    assert "urgent" in r.outcome_flags  # the team is alerted too


def test_the_pdf_version_imports_the_same_questions_and_answers(pairs):
    from app.services import knowledge_import

    got, ignored = knowledge_import.import_pdf(FILE.with_suffix(".pdf").read_bytes())
    assert ignored == 0  # nothing in our own file is left out
    norm = lambda t: " ".join(t.split())
    assert {(norm(q), norm(a)) for q, a in got} == {(norm(q), norm(a)) for q, a in pairs}

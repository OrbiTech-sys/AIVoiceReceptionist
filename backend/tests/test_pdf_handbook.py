"""A staff handbook / template PDF must not be read out to callers: headers, footers, blank fields and instructions are dropped."""
from __future__ import annotations

import pytest

from app.errors import AppError
from app.services import knowledge_import as ki
from app.voice.tools import TenantSnapshot  # noqa: F401  (import check only)

HEADER = "Dental Receptionist Knowledge Base | Customize and verify before use"


def pdf(pages: list[list[str]]) -> bytes:
    """A small PDF with the given lines of text on each page."""
    objs: list[str] = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    font = 3 + 2 * len(pages)
    for i, lines in enumerate(pages):
        content = "BT /F1 11 Tf 13 TL 50 740 Td " + " ".join(f"({ln}) Tj T*" for ln in lines) + " ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>")
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()


HANDBOOK = pdf([
    [HEADER + " Page 1", "Clinic name: [Enter clinic name] Phone: [Enter phone number] Address: [Enter clinic address]",
     "Appointment Management", "Ask for the patient's full name, best contact number, and reason for the visit.",
     "Notify the dentist promptly if the caller reports severe pain."],
    [HEADER + " Page 2", "1. Do you offer teeth whitening?", "Yes. Professional whitening takes about an hour and the dentist checks your teeth first.",
     "2. Where can I park?", "Free parking is available behind the building on Oak Street."],
    [HEADER + " Page 3", "Q: How much does a cleaning cost? A: Prices depend on the visit. I can ask the team to confirm the current fee.",
     "Do not disclose patient information to family members without authorization.", "Page 3"],
])


def test_headers_footers_blank_fields_and_staff_instructions_are_left_out():
    entries, ignored = ki.import_pdf(HANDBOOK)
    text = " ".join(q + " " + a for q, a in entries)
    for junk in ("Customize and verify", "Page 1", "Page 3", "[Enter", "Ask for the patient", "Notify the dentist", "Do not disclose"):
        assert junk not in text, junk
    assert ignored >= 1  # the template-fields passage (staff instructions inside real answers are cut out, not counted)


def test_what_is_real_is_kept_with_clean_questions():
    entries, _ = ki.import_pdf(HANDBOOK)
    by_q = dict(entries)
    assert by_q["Do you offer teeth whitening?"].startswith("Yes. Professional whitening takes about an hour")  # "1." removed
    assert "Oak Street" in by_q["Where can I park?"]
    assert by_q["How much does a cleaning cost?"].startswith("Prices depend on the visit.")  # a written-out Q: / A: pair


def test_a_pdf_with_nothing_sayable_explains_what_to_do():
    only_template = pdf([["Clinic name: [Enter clinic name] Phone: [Enter phone number] Address: [Enter clinic address]"],
                         ["Ask for the patient's full name, best contact number, and reason for the visit."]])
    with pytest.raises(AppError) as e:
        ki.import_pdf(only_template)
    assert e.value.code == "empty_import" and "template fields" in e.value.message


@pytest.mark.parametrize("q, a, ok", [
    ("Do you take walk-ins?", "Appointments are recommended, but we will do our best to fit you in.", True),
    ("Parking", "Free parking is available behind the building.", True),
    ("Please bring", "Please bring a photo ID and your insurance card.", True),
    ("Clinic", "Clinic name: [Enter clinic name] Phone: [Enter phone number]", False),
    ("Greeting", "Thank you for calling [Clinic Name]. How may I help you?", False),
    ("Intake", "Ask for the patient's name. Confirm the phone number. Notify the dentist.", False),
    ("Privacy", "Do not disclose patient information to family members without authorization.", False),
    ("Header", "Dental Receptionist Knowledge Base | Customize and verify before use Page 2 text", False),
    ("Bullets", "\x7f Opening hours are 9 to 5.", False),
    ("Escalation", "Escalate clinical and emergency calls to the dentist, following the clinic protocol.", False),
])
def test_what_may_be_said_to_a_caller(q, a, ok):
    assert ki.is_speakable(q, a) is ok

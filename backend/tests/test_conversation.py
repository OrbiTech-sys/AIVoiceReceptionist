"""Things that went wrong on a real test call, replayed line by line."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import numpy as np
import pytest
from test_auth_tenancy import _next_slot
from test_voice import snapshot

from app.db import SessionLocal
from app.models import Appointment, Tenant
from app.voice import engines, nlu
from app.voice.dialog import Receptionist
from app.voice.session import CallSession
from app.voice.tools import ApptInfo, ReceptionistTools, SlotOption, ToolResult

NOW = datetime(2026, 11, 5, 17, 45, tzinfo=timezone.utc)  # a Thursday, 12:45 in New York
SERVICES = [{"id": "consult", "name": "Consultation", "duration": 30, "price": "", "description": ""},
            {"id": "clean", "name": "Dental cleaning", "duration": 60, "price": "", "description": ""}]
KB = [{"id": "k1", "kind": "faq", "q": "Do you see children?", "a": "Yes, we see patients of all ages."}]
THURSDAY = date(2026, 11, 5)


class OneSlotTools:
    def __init__(self):
        self.booked = []
        self.callbacks = []

    def find_slots(self, service_id, when, limit=3, exclude_appointment_id=None):
        return [SlotOption(datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc), None, None)]  # today at 4 PM

    def book(self, service_id, slot, name, phone, notes=""):
        self.booked.append((service_id, name, phone))
        return ToolResult(True, appointment=ApptInfo("a1", "Consultation", service_id, slot.start, None))

    def find_appointments(self, phone):
        return []

    def request_callback(self, name, phone, reason, urgent=False):
        self.callbacks.append((name, phone, reason))
        return ToolResult(True)


def _receptionist(**kw) -> tuple[Receptionist, OneSlotTools]:
    tools = OneSlotTools()
    return Receptionist(snapshot(services=SERVICES, kb=KB), tools, now=NOW, **kw), tools


def _reach_phone_question(r: Receptionist) -> None:
    for line in ("I want to book appointment.", "Consultation", "Yes, I have that.", "4pm.", "Yes?", "My full name is Abdul Basit."):
        r.handle(line)
    assert r.state == "book_phone"


# ------------------------------------------------------------------ the booking must survive
def test_number_said_in_pieces_still_books_the_appointment():
    r, tools = _receptionist()
    _reach_phone_question(r)
    assert r.handle("My phone number is...").text == "Sure, go ahead."
    assert r.handle("0 3 1 9").text == "Okay, go on."
    assert r.handle("1 5 3").text == "Okay, go on."
    assert "0 3 1 9, 1 5 3, 3 9 7 7" in r.handle("3 9 7 7").text
    assert "Shall I book" in r.handle("Yes.").text
    assert "You're all set" in r.handle("Yes.").text
    assert tools.booked == [("consult", "Abdul Basit", "03191533977")]
    assert tools.callbacks == []


def test_a_full_number_replaces_earlier_pieces():
    r, _ = _receptionist()
    _reach_phone_question(r)
    r.handle("9 2")
    assert "4 1 5, 5 5 5, 0 1 0 0" in r.handle("415 555 0100").text


def test_giving_up_on_a_booking_says_so_and_passes_on_the_request():
    r, tools = _receptionist()
    _reach_phone_question(r)
    r.handle("blorp")
    r.handle("zzzt")
    reply = r.handle("qwerty").text
    assert "haven't booked anything" in reply and "call you back" in reply
    assert r.state == "msg_phone"
    assert "Is that correct" in r.handle("415 555 0100").text
    done = r.handle("Yes.").text
    assert "regarding" not in done.lower() and "anything else" in done  # it already knows what the caller wanted
    assert not tools.booked
    name, phone, reason = tools.callbacks[0]
    assert name == "Abdul Basit" and phone == "+14155550100"
    assert "consultation" in reason and "today at 4 PM" in reason


def test_caller_cannot_get_stuck_on_one_question_forever():
    r, _ = _receptionist()
    r.handle("I want to book appointment.")
    assert r.state == "book_service"
    r.handle("Appignment, Later.")
    r.handle("Long to call.")
    assert "haven't booked anything" in r.handle("blorp").text
    assert r.state == "msg_name"


def test_taking_a_message_ends_politely_if_nothing_can_be_understood():
    r, _ = _receptionist()
    _reach_phone_question(r)
    for line in ("blorp", "zzzt", "qwerty", "blorp", "zzzt"):
        assert r.handle(line).end_call is False
    final = r.handle("qwerty")
    assert final.end_call is True and "call back" in final.text
    assert r.ended


def test_real_phone_caller_with_a_transfer_number_reaches_a_person():
    tools = OneSlotTools()
    r = Receptionist(snapshot(services=SERVICES, transfer_number="+14155550111"), tools, channel="phone", now=datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc))
    r.handle("blorp")
    r.handle("zzzt")
    assert r.handle("qwerty").transfer is True


# ------------------------------------------------------------------ small things a caller naturally says
def test_hello_is_not_a_mistake():
    r, _ = _receptionist()
    reply = r.handle("Hello.").text
    assert "Sorry" not in reply and "How can I help" in reply and r.misunderstood == 0
    r.handle("I want to book appointment.")
    r.handle("Consultation")
    reply = r.handle("Can you hear me?").text
    assert "Sorry" not in reply and "what day or time" in reply


def test_yes_to_having_a_preferred_time_just_asks_for_it():
    r, _ = _receptionist()
    r.handle("I want to book appointment.")
    r.handle("Consultation")
    reply = r.handle("Yes, I have that.").text
    assert "Sorry" not in reply and "what day and time" in reply and r.state == "book_when" and r.misunderstood == 0


@pytest.mark.parametrize("line", ["End of call.", "End the call", "Please hang up", "Wrong number", "Goodbye"])
def test_caller_can_end_the_call_in_the_middle_of_a_booking(line):
    r, _ = _receptionist()
    r.handle("I want to book appointment.")
    assert r.handle(line).end_call is True


def test_asking_about_an_appointment_during_a_booking_looks_it_up():
    r, _ = _receptionist()
    r.handle("I want to book appointment.")
    assert "phone number" in r.handle("Could you please tell me about that appointment?").text
    assert r.state == "lookup_phone" and r.flow == "check"


def test_cancelling_an_appointment_during_a_booking_looks_it_up():
    r, _ = _receptionist()
    r.handle("I want to book appointment.")
    r.handle("Can I cancel my appointment instead?")
    assert r.state == "lookup_phone" and r.flow == "cancel"


def test_message_question_uses_plain_words():
    r, _ = _receptionist()
    for line in ("I'd like to talk to a real person", "Abdul Basit", "415 555 0100"):
        r.handle(line)
    reply = r.handle("Yes.").text
    assert "regarding" not in reply.lower() and "call you about" in reply
    assert r._reprompt() == "What should we call you about?"


# ------------------------------------------------------------------ answers must actually match
def test_unrelated_words_do_not_trigger_a_faq_answer():
    r, _ = _receptionist()
    assert "all ages" not in r.handle("I see what is").text
    assert "all ages" in r.handle("Do you see children?").text


# ------------------------------------------------------------------ listening
def test_receptionist_waits_longer_when_people_pause_mid_answer():
    r, _ = _receptionist()
    assert r.end_of_turn_ms == 650
    r.state = "book_name"
    assert r.end_of_turn_ms == 1000
    for state in ("book_phone", "msg_phone", "lookup_phone"):
        r.state = state
        assert r.end_of_turn_ms == 1500


class _LoudnessVAD:
    frame_samples = 512

    def prob(self, frame):
        return 1.0 if np.abs(frame).max() > 0.01 else 0.0

    def reset(self):
        pass


def _turn_ends(detector: engines.TurnDetector) -> int:
    def block(level, seconds):
        return np.full(int(engines.PIPELINE_SR * seconds), level, dtype=np.float32)

    audio = np.concatenate([block(0.5, 0.5), block(0.0, 1.0), block(0.5, 0.5), block(0.0, 2.0)])  # a one-second pause
    return sum(ev.kind == "turn_end" for ev in detector.feed(audio))


def test_turn_detector_treats_a_pause_as_part_of_the_turn_when_told_to():
    assert _turn_ends(engines.TurnDetector(_LoudnessVAD())) == 2
    patient = engines.TurnDetector(_LoudnessVAD())
    patient.set_end_silence_ms(1500)
    assert _turn_ends(patient) == 1


def test_call_listens_longer_while_a_number_is_being_given():
    r, _ = _receptionist()
    r.state = "book_phone"

    class Quiet:
        async def send_event(self, event):
            pass

    async def scenario():
        call = CallSession("t1", Quiet(), channel="browser_demo")
        call.dialog = r
        call.turns = engines.TurnDetector(_LoudnessVAD())
        before = call.turns.end_silence_frames
        await call.on_audio(np.zeros(512, dtype=np.float32), 16000)
        return before, call.turns.end_silence_frames

    before, during = asyncio.run(scenario())
    assert during > before * 2


def test_speech_recognition_is_told_what_the_caller_is_likely_saying():
    r, _ = _receptionist()
    assert "Bright Smile" in r.asr_hint() and "Consultation" in r.asr_hint()
    r.state = "book_phone"
    assert "digit" in r.asr_hint()
    r.state = "book_name"
    assert "name" in r.asr_hint()


# ------------------------------------------------------------------ phone numbers
@pytest.mark.parametrize("said, expected", [
    ("415 555 0100", "+14155550100"),
    ("1 415 555 0100", "+14155550100"),
    ("0 3 1 9 1 5 double 3 9 double 7", "03191533977"),  # local format: never guess a country code
    ("0412 345 678", "0412345678"),
    ("+92 319 1533977", "+923191533977"),
    ("9, 2,", None),
])
def test_phone_numbers(said, expected):
    assert nlu.extract_phone(said) == expected


def test_appointment_is_booked_even_if_the_number_cannot_be_filed(owner):
    service = owner.get("/api/services").json()[0]
    start = datetime.fromisoformat(_next_slot(owner, service["id"]).replace("Z", "+00:00"))
    with SessionLocal() as db:
        tenant_id = db.query(Tenant).one().id
    result = ReceptionistTools(tenant_id, None).book(service["id"], SlotOption(start, None, None), "Abdul Basit", "03191533977")
    assert result.ok, result.message
    with SessionLocal() as db:
        appointment = db.query(Appointment).one()
        assert appointment.contact.name == "Abdul Basit" and appointment.contact.phone_e164 is None
        assert "03191533977" in appointment.notes


# ------------------------------------------------------------------ yes, no, dates and emergencies
@pytest.mark.parametrize("line", ["Yes", "Yes?", "Mm-hmm.", "That's fine.", "Sounds great.", "That'd be great", "Go for it", "I think so",
                                  "No problem", "No, that works", "Yeah, that works"])
def test_ways_of_saying_yes(line):
    assert nlu.is_affirm(line) and not nlu.is_deny(line)


@pytest.mark.parametrize("line", ["No", "No.", "Nope", "Not really", "No, I can't"])
def test_ways_of_saying_no(line):
    assert nlu.is_deny(line) and not nlu.is_affirm(line)


@pytest.mark.parametrize("line, expected", [
    ("May I book for Tuesday?", date(2026, 11, 10)),
    ("May I come in on Friday afternoon?", date(2026, 11, 6)),
    ("May I come in tomorrow?", date(2026, 11, 6)),
    ("May 12", date(2027, 5, 12)),
    ("Friday", date(2026, 11, 6)),
    ("Next Friday afternoon", date(2026, 11, 13)),
    ("Tuesday next week", date(2026, 11, 10)),
])
def test_dates(line, expected):
    assert nlu.parse_when(line, THURSDAY).dates == [expected]


def test_next_monday_said_on_a_sunday_means_tomorrow():
    assert nlu.parse_when("next Monday", date(2026, 11, 8)).dates == [date(2026, 11, 9)]


@pytest.mark.parametrize("line", ["My gums won't stop bleeding", "My face is really swollen and it hurts a lot", "I can't breathe",
                                  "This is an emergency", "I have severe pain"])
def test_real_emergencies_are_recognised(line):
    assert nlu.is_emergency(line, True)


@pytest.mark.parametrize("line", ["Do you do emergency appointments?", "Do you see emergency patients on weekends?", "Do you offer emergency care?"])
def test_asking_about_emergency_services_is_not_an_emergency(line):
    assert not nlu.is_emergency(line, True)


# ------------------------------------------------------------------ second test call (2026-11-06)
FRIDAY_NOON = datetime(2026, 11, 6, 17, 0, tzinfo=timezone.utc)  # 12 PM in New York


class ThreeSlotTools(OneSlotTools):
    def find_slots(self, service_id, when, limit=3, exclude_appointment_id=None):
        # 9 AM in New York: tomorrow (Saturday), Monday and Tuesday
        return [SlotOption(datetime(2026, 11, d, 14, 0, tzinfo=timezone.utc), None, None) for d in (7, 9, 10)]


def _second_call() -> tuple[Receptionist, ThreeSlotTools]:
    tools = ThreeSlotTools()
    return Receptionist(snapshot(services=SERVICES, kb=KB), tools, now=FRIDAY_NOON), tools


def test_second_call_replayed_ends_in_a_booking():
    r, tools = _second_call()
    assert "May I have your name" in r.handle("Can I talk to the doctor?", 0.78).text
    # "from" is not a name, and a name heard with low confidence is checked
    assert r.handle("Yeah, my name is from Hij.", 0.33).text == "I have your name as Hij. Is that right?"
    assert "What's the best number" in r.handle("Yes.", 0.9).text
    assert "2 1 2, 7 8 7, 4 1 2 8" in r.handle("It's 2, 1, 2, 7, 8, 7, 4, 1, 2, 8.", 0.78).text
    # Confirming the number and asking to book switches to booking instead of asking what to call about
    reply = r.handle("Yes, it's correct. I want to book an appointment. Can you make one?", 0.89).text
    assert "What would you like to come in for" in reply
    assert r.handle("Consultation.", 0.56).text == "Great, a consultation. Do you have a preferred day or time?"
    offer = r.handle("No police is your hand.", 0.19).text
    assert offer == "I have 9 AM tomorrow, Monday, or Tuesday. Which works best for you?"
    assert r.handle("Pull in the T-shirt and check out the code.", 0.2).text.startswith("Sorry, the line wasn't clear.")
    assert r.handle("I'm going to go to the end of the video.", 0.2).text.startswith("Sorry, the line wasn't clear.")
    # Wanting to book, without saying which time, offers the times again rather than giving up
    assert r.handle("Yeah, I want to book it forward.", 0.73).text == \
        "Sure. I have 9 AM tomorrow, Monday, or Tuesday. Which one would you like?"
    # Name and number are already known, so it goes straight to the confirmation
    confirm = r.handle("I want to work it for 9am Monday.", 0.77).text
    assert confirm == "Let me confirm: a consultation Monday, November 9th at 9 AM, for Hij. Shall I book that for you?"
    assert "You're all set" in r.handle("Yes.", 0.9).text
    assert tools.booked == [("consult", "Hij", "+12127874128")]
    assert tools.callbacks == []


def test_giving_up_on_a_booking_does_not_ask_for_the_number_again():
    r, tools = _second_call()
    r.handle("Can I talk to the doctor?")
    r.handle("My name is Sara Khan.")
    r.handle("2 1 2 7 8 7 4 1 2 8")
    r.handle("Yes, and I'd like to book a consultation.")
    r.handle("Any time.")
    for garbled in ("Pull in the T-shirt.", "End of the video.", "Purple monkey dishwasher."):
        reply = r.handle(garbled, 0.2).text
    assert "I haven't booked anything yet" in reply
    assert "number" not in reply  # it already has one
    assert tools.callbacks and tools.callbacks[-1][:2] == ("Sara Khan", "+12127874128")
    assert tools.booked == []


def test_times_on_different_days_are_offered_briefly():
    r, _ = _second_call()
    r.handle("I'd like to book a consultation.")
    assert r.handle("Whenever suits.").text == "I have 9 AM tomorrow, Monday, or Tuesday. Which works best for you?"


def test_leaving_a_message_can_turn_into_a_booking():
    r, tools = _second_call()
    r.handle("Can someone call me back?")
    r.handle("This is Sara Khan.")
    r.handle("2 1 2 7 8 7 4 1 2 8")
    r.handle("Yes.")
    assert "Do you have a preferred day" in r.handle("Actually, can I book a consultation?").text
    assert r.flow == "book"

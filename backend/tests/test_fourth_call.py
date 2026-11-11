"""Problems from real test calls on 2026-11-08, replayed with the callers' own sentences."""
from __future__ import annotations

import asyncio
from datetime import date

import numpy as np
import pytest
from test_conversation import ThreeSlotTools, _second_call
from test_hosted_services import FakeLLM

from app.voice import engines, nlu
from app.voice.dialog import Receptionist
from app.voice.session import CallSession
from app.voice.tools import ApptInfo, ToolResult


class RecordingTools(ThreeSlotTools):
    def book(self, service_id, slot, name, phone, notes="", email=""):
        self.booked.append((service_id, name, phone, email))
        return ToolResult(True, appointment=ApptInfo("a1", "Dental cleaning", service_id, slot.start, None))


def _call(llm=None) -> tuple[Receptionist, RecordingTools]:
    r, _ = _second_call()
    tools = RecordingTools()
    return Receptionist(r.snap, tools, now=r.now, llm=llm), tools


def test_changing_the_time_while_giving_details_goes_back_to_the_times_and_a_caller_with_no_phone_can_give_an_email():
    r, tools = _call()
    r.handle("Yeah. I want to work an appointment.", 1.0)
    assert r.state == "book_service"
    assert r.handle("Yes. I am still there. I was talking about an appointment for dental cleaning.", 1.0).text.startswith("Great, a dental cleaning")
    assert "Which works best" in r.handle("How about Monday?", 1.0).text
    assert r.handle("Do it 9 AM Monday.", 1.0).text.endswith("May I have your full name, please?")
    assert r.state == "book_name"
    # a long sentence about the time, said while it waits for a name, is not taken for a name
    reply = r.handle("Sorry. Can you make it to 4PM instead of 06:30?", 1.0).text
    assert reply.startswith("Sure.") and r.state == "book_pick"
    assert r.handle("The first one.", 1.0).text.endswith("May I have your full name, please?")
    reply = r.handle("I said, can you make the appointment time from 03:30 to 4PM?", 1.0).text
    assert reply.startswith("Sure.") and r.state == "book_pick"  # and certainly not named "Said"
    r.handle("The first one.", 1.0)
    # a correction: the last "my name is" wins
    assert r.handle("My name is not set. My name is Abar.", 1.0).text == "Thanks, Abar. What's the best phone number to reach you?"
    reply = r.handle("I don't have a phone number, but I do have an email if you can email me.", 1.0).text
    assert reply == "No problem. What's the best email address to reach you?" and r.state == "book_email"
    reply = r.handle("It's nova@gmail.com.", 1.0).text
    assert reply == "I have n o v a at gmail dot com. Is that correct?"  # not "Thanks, Nova Gmail"
    assert "Shall I book that for you?" in r.handle("Yes.", 1.0).text and r.state == "book_confirm"
    assert "You're all set" in r.handle("Yes.", 1.0).text
    assert tools.booked[-1][1:] == ("Abar", None, "nova@gmail.com")


def test_an_email_can_be_said_the_spoken_way_and_corrected():
    r, _ = _call()
    for line in ("I want to book a dental cleaning.", "Any time.", "The first one.", "My name is Sara Khan."):
        r.handle(line, 1.0)
    assert r.state == "book_phone"
    assert r.handle("No phone. My email is sara dot khan at gmail dot com.", 1.0).text == "I have s a r a dot k h a n at gmail dot com. Is that correct?"
    assert r.handle("No, that's wrong.", 1.0).state == "book_email"
    r.handle("It's sara at gmail dot com.", 1.0)
    assert r.b.email == "sara@gmail.com" and r.state == "book_email_confirm"


def test_asking_whether_the_doctor_is_free_is_not_taken_for_booking():
    r, tools = _call()
    r.handle("Can I talk to the doctor?", 1.0)
    assert r.state == "msg_name"
    reply = r.handle("Yes. Yes. I'm here. Yeah. Actually, I wanted to talk to doctor Mayapertil. I was asking, is she available right now?", 1.0)
    assert r.flow == "message" and "What would you like to come in for" not in reply.text
    assert "May I have your name" in reply.text
    assert nlu.detect_intents("Is the doctor available tomorrow?") == ["book"]  # but a question about times still is


def test_a_long_sentence_the_rules_cannot_place_is_understood_by_the_language_helper():
    llm = FakeLLM(("human", None))
    r, _ = _call(llm)
    r.handle("I want to book an appointment.", 1.0)
    assert r.state == "book_service"
    long = "Actually, I just want to talk about some suggestion I want to take from her, if you can give me some time with the doctor."
    reply = r.handle(long, 1.0).text
    assert reply.startswith("Of course. I'll have a member of our team call you back") and llm.asked == [long]
    assert r.misunderstood == 0


def test_without_the_language_helper_a_long_unclear_sentence_still_gets_a_sensible_question():
    r, _ = _call()
    r.handle("I want to book an appointment.", 1.0)
    reply = r.handle("Actually I just want to talk about some suggestion I want to take from her if you can give me time.", 1.0).text
    assert "which service" in reply.lower() or "come in for" in reply.lower()


# ------------------------------------------------------------------ the session: a failed hearing request must not end the call
class _Hearing:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)

    def transcribe(self, audio, hints=""):
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return engines.Transcript(out, 0.99, 5)


class _Log:
    def __init__(self):
        self.said, self.heard = [], []

    async def send_event(self, e):
        if e.get("type") == "transcript":
            (self.said if e["role"] == "agent" else self.heard).append(e["text"])

    async def send_audio(self, a, sr): pass

    async def stop_audio(self): pass


def _hear(outcomes):
    r, _ = _call()
    log = _Log()

    async def scenario():
        call = CallSession("t1", log, channel="browser_demo")
        call.dialog, call.asr = r, _Hearing(*outcomes)
        for _ in outcomes:
            await call._process_audio_turn(np.ones(16000, dtype=np.float32))
        return call

    return asyncio.run(scenario()), log


def test_one_or_two_failed_recognitions_only_ask_the_caller_to_repeat():
    call, log = _hear([RuntimeError("slow"), RuntimeError("slow"), "I want to book an appointment."])
    assert log.said[:2] == ["Sorry, I didn't catch that. Could you say it again?"] * 2
    assert log.heard == ["I want to book an appointment."] and call._failed is None  # and the call carried on


def test_three_failures_in_a_row_hand_over_to_the_team():
    call, log = _hear([RuntimeError("down")] * 3)
    assert call._failed and "technical difficulties" in log.said[-1]


# ------------------------------------------------------------------ less audio to upload
def test_the_quiet_after_the_last_word_is_trimmed_before_upload():
    speech = (np.sin(np.linspace(0, 600, 16000)) * 0.3).astype(np.float32)
    audio = np.concatenate([np.zeros(4000, np.float32), speech, np.zeros(24000, np.float32)])  # 1.5 s of quiet after
    out = engines.trim_trailing_silence(audio)
    assert 4000 + 16000 < out.size < 4000 + 16000 + 6000  # about 0.3 s of the quiet is kept
    assert (out[:20000] == audio[:20000]).all()  # speech untouched
    silent = np.zeros(16000, np.float32)
    assert engines.trim_trailing_silence(silent).size == silent.size  # nothing to find: unchanged

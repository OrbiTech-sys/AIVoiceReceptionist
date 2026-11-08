"""The third test call (2026-11-08), replayed line by line, plus waiting for the end of a sentence."""
from __future__ import annotations

import asyncio

import numpy as np
import pytest
from test_conversation import _receptionist, _second_call

from app.voice import engines, nlu
from app.voice.session import CallSession


def _third_call():
    return _second_call()


def test_repeating_what_they_want_offers_times_instead_of_giving_up():
    r, tools = _third_call()
    r.handle("I want to book an appointment.", 0.99)
    assert r.handle("for consultation.", 0.91).text == "Great, a consultation. Do you have a preferred day or time?"
    offer = r.handle("I want consultation.", 0.85).text
    assert offer == "Sure. I have 9 AM tomorrow, Monday, or Tuesday. Which works best for you?"
    assert r.handle("Monday.", 0.83).text.startswith("Great, Monday, November 9th at 9 AM.")


def test_a_name_heard_wrong_is_read_back_and_can_be_corrected_at_once():
    r, tools = _third_call()
    for line in ("I want to book a consultation.", "Whenever.", "Monday."):
        r.handle(line, 0.9)
    assert r.handle("of Bill Bassett.", 0.75).text == "I have your name as Bill Bassett. Is that right?"
    reply = r.handle("No, my name is Akil Basit.", 0.76).text
    assert reply.startswith("Thanks, Akil.") and "number" in reply
    assert "0 3 1, 9 1 5, 3 3 9 7" in r.handle("My number is 0319153397.", 0.96).text
    # A piece of a number can't be fitted into the one read back: start the number over
    again = r.handle("9 double seven", 0.8).text
    assert "whole number" in again and r.state == "book_phone"
    assert r.handle("Not", 0.7).text  # and a bare "not" is a no, not noise


def test_giving_up_on_a_day_offers_openings_and_a_later_day_resumes_the_booking():
    r, tools = _third_call()
    r.handle("I want to book a consultation.", 0.9)
    assert r.state == "book_when"
    for garbled in ("But they.", "Purple monkey dishwasher.", "Yellow banana telephone."):
        reply = r.handle(garbled, 0.3).text
    assert reply == "Let me just offer the next openings. I have 9 AM tomorrow, Monday, or Tuesday. Which works best for you?"
    for garbled in ("Purple monkey dishwasher.", "Yellow banana telephone.", "Green chair lamp."):
        reply = r.handle(garbled, 0.3).text
    assert "I haven't booked anything yet" in reply and r.state == "msg_name"
    # A day arrives while it is asking for a name: the booking carries on from where it was
    assert r.handle("Monday.", 0.83).text.startswith("Okay, let's go back to your booking.")
    assert r.flow == "book" and r.b.service_id == "consult"


def test_what_appointment_did_you_book_is_answered_honestly():
    r, tools = _third_call()
    r.handle("Can I talk to the doctor?")
    r.handle("My name is Sara Khan.", 0.95)
    r.handle("2 1 2 7 8 7 4 1 2 8")
    r.handle("Yes.")
    r.handle("About my teeth.")
    reply = r.handle("What apartment did you book?").text
    assert reply.startswith("I haven't booked an appointment yet. I've passed your details to our team")
    assert r.handle("Yes please.").text.startswith("Of course!")
    assert tools.booked == []


def test_speech_recognition_mixups_are_corrected():
    assert nlu.fix_mishearings("I want an apartment on Monday") == "I want an appointment on Monday"
    assert "booking_status" in nlu.detect_intents("What apartment did you book?")
    assert nlu.is_deny("Not") and nlu.is_deny("not correct") and not nlu.is_deny("Notice me")


@pytest.mark.parametrize("said, unfinished", [
    ("I want", True), ("Can you please", True), ("What apartment did you", True), ("I'd like to", True),
    ("Yes, please.", False), ("I want to book an appointment", False), ("Consultation", False), ("Monday.", False),
    ("No.", False), ("Hello", False), ("2 1 2 7 8 7 4 1 2 8", False),
])
def test_stopping_mid_sentence_is_recognised(said, unfinished):
    assert nlu.looks_unfinished(said) is unfinished


class _Scripted:
    """Speech recognition that returns the given texts, one per turn."""

    def __init__(self, *texts):
        self.texts = list(texts)

    def transcribe(self, audio, hint=""):
        return engines.Transcript(self.texts.pop(0), 0.9, 5)


class _Events:
    def __init__(self):
        self.heard = []

    async def send_event(self, event):
        if event.get("type") == "transcript" and event.get("role") == "caller":
            self.heard.append(event["text"])

    async def send_audio(self, audio, sample_rate):
        pass

    async def stop_audio(self):
        pass


def _hold_scenario(texts, waits):
    r, _ = _receptionist()
    events = _Events()

    async def scenario():
        call = CallSession("t1", events, channel="browser_demo")
        call.dialog, call.asr = r, _Scripted(*texts)
        call.HOLD_UNFINISHED_S = 0.2
        audio = np.ones(16000, dtype=np.float32)  # a second of "speech"
        for wait in waits:
            await call._process_audio_turn(audio)
            await asyncio.sleep(wait)
        call._closed = True
        call._cancel_held_timer()

    asyncio.run(scenario())
    return events.heard


def test_the_rest_of_a_sentence_is_waited_for_and_joined():
    assert _hold_scenario(["I want", "for consultation."], [0.0, 0.05]) == ["I want for consultation."]


def test_a_sentence_left_unfinished_is_answered_after_a_short_wait():
    assert _hold_scenario(["Can you please"], [0.5]) == ["Can you please"]

"""Receptionist conversation state machine.

Decisions (what to ask, which tool to call, what to confirm) are deterministic. An optional
local LLM may only *classify* an utterance into one of a fixed set of intents / knowledge
entries; it never produces spoken text and never calls tools.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from ..config import get_settings
from ..services.timeutil import local_today, spoken_date, spoken_time, zone
from . import nlu
from .tools import ApptInfo, ReceptionistTools, SlotOption, TenantSnapshot

log = logging.getLogger("voicerep.dialog")

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# Booking steps after a time was chosen, where the caller may still ask for another time.
LATE_BOOKING_STATES = ("book_name", "book_name_confirm", "book_phone", "book_phone_callerid", "book_phone_confirm", "book_email",
                       "book_email_confirm", "book_confirm")
# Names are what speech recognition gets wrong most often, so unless it was very sure, the name is read back.
NAME_SURE = 0.9


@dataclass
class Reply:
    text: str
    end_call: bool = False
    transfer: bool = False
    state: str = ""


@dataclass
class Booking:
    service_id: str | None = None
    when: nlu.When | None = None
    options: list[SlotOption] = field(default_factory=list)
    slot: SlotOption | None = None
    name: str | None = None
    phone: str | None = None
    appointment: ApptInfo | None = None  # for reschedule / cancel
    candidates: list[ApptInfo] = field(default_factory=list)
    reason: str = ""
    email: str = ""  # given instead of a phone number
    reason_final: bool = False  # the reason is already known, so don't ask the caller for one
    partial_digits: str = ""  # a phone number the caller is giving in pieces


class Receptionist:
    def __init__(self, snap: TenantSnapshot, tools: ReceptionistTools, *, caller_number: str | None = None,
                 channel: str = "browser_demo", llm=None, agent=None, now: datetime | None = None):
        self.snap = snap
        self.tools = tools
        self.caller_number = caller_number
        self.channel = channel
        self.llm = llm
        self.agent = agent  # the optional LangGraph agent (app/voice/agent.py)
        self._asked_again = False  # the agent asked the caller to clarify: that turn still counts as not understood
        self._now = now
        self.tz = zone(snap.timezone)
        self.state = "idle"
        self.flow = ""  # book | reschedule | cancel | message
        self.b = Booking()
        self.last_reply = ""
        self.misunderstood = 0
        self.intents_seen: list[str] = []
        self.actions: list[str] = []
        self.ended = False
        self.wants_transfer = False
        self.outcome_flags: set[str] = set()
        self.caller_name: str | None = None
        self.caller_phone: str | None = None  # a number given earlier in this call
        self.phone_confirmed = True  # false while the number was only picked up from a long sentence and still needs reading back
        self.llm_calls = 0  # LLM questions asked so far in this call
        self.llm_max_calls = get_settings().llm_calls_per_call
        self.relists = 0  # times the offered times were repeated because the caller wanted one but didn't say which
        self.resume_service: str | None = None  # what the caller wanted to book when the booking was given up on
        self.fallback_offered = False  # the next openings were offered because the caller couldn't give a day

    # ------------------------------------------------------------------ helpers
    @property
    def now(self) -> datetime:
        return self._now or datetime.now(timezone.utc)

    @property
    def today(self) -> date:
        return local_today(self.tz, self.now)

    def _say(self, text: str, **kw) -> Reply:
        text = re.sub(r"\s+", " ", text).strip()
        self.last_reply = text
        return Reply(text, state=self.state, **kw)

    def _when_text(self, dt: datetime, with_day: bool = True) -> str:
        local = dt.astimezone(self.tz)
        t = spoken_time(local)
        return f"{spoken_date(local.date(), self.today)} at {t}" if with_day else t

    def _service(self, sid: str | None) -> dict | None:
        return next((s for s in self.snap.services if s["id"] == sid), None)

    def _service_name(self, sid: str | None) -> str:
        s = self._service(sid)
        return s["name"].lower() if s else "appointment"

    def _hours_for(self, d: date) -> list[tuple[time, time]]:
        if d in self.snap.holidays:
            return self.snap.holidays[d][1]
        return self.snap.hours.get(d.weekday(), [])

    def is_open_now(self) -> bool:
        local = self.now.astimezone(self.tz)
        return any(a <= local.time() < b for a, b in self._hours_for(local.date()))

    def _fmt_intervals(self, iv: list[tuple[time, time]]) -> str:
        if not iv:
            return "closed"
        if len(iv) == 2:
            return f"{spoken_time(iv[0][0])} to {spoken_time(iv[1][1])}, closed from {spoken_time(iv[0][1])} to {spoken_time(iv[1][0])}"
        return f"{spoken_time(iv[0][0])} to {spoken_time(iv[0][1])}"

    def hours_summary(self) -> str:
        groups: list[tuple[list[int], str]] = []
        for wd in range(7):
            desc = self._fmt_intervals(self.snap.hours.get(wd, []))
            if groups and groups[-1][1] == desc and groups[-1][0][-1] == wd - 1:
                groups[-1][0].append(wd)
            else:
                groups.append(([wd], desc))
        parts = []
        for days, desc in groups:
            if len(days) == 1:
                label = DAY_NAMES[days[0]]
            elif len(days) == 2:
                label = f"{DAY_NAMES[days[0]]} and {DAY_NAMES[days[1]]}"
            else:
                label = f"{DAY_NAMES[days[0]]} through {DAY_NAMES[days[-1]]}"
            parts.append(f"{label}, {desc}" if desc != "closed" else f"closed {label}")
        if all(desc == "closed" for _, desc in groups):
            return "I don't have our opening hours on file yet."
        return "We're open " + "; ".join(parts) + "."

    # ------------------------------------------------------------------ public API
    @property
    def end_of_turn_ms(self) -> int:
        """How long a pause ends the caller's turn. People pause while giving a number or a name."""
        if self.state in ("book_phone", "lookup_phone", "msg_phone"):
            return 1500
        if self.state in ("book_name", "msg_name", "msg_reason"):
            return 1000
        return 650

    def asr_hint(self) -> str:
        """What the caller is probably about to say, to help speech recognition."""
        if self.state in ("book_phone", "lookup_phone", "msg_phone"):
            return "Phone number, spoken digit by digit."
        if self.state in ("book_name", "msg_name"):
            return "The caller's full name."
        services = ", ".join(s["name"] for s in self.snap.services[:8])
        return f"{self.snap.name}. {services}."

    def greeting(self) -> Reply:
        s = self.snap
        greet = s.greeting.strip() or f"Thank you for calling {s.name}. How can I help you today?"
        pre = []
        if s.recording_enabled and s.recording_consent:
            pre.append(s.recording_consent.strip())
        if s.ai_disclosure and not re.search(r"\b(virtual|ai|automated|digital) (assistant|receptionist)\b", greet, re.I):
            greet = greet.replace("How can I help", f"I'm {s.receptionist_name}, a virtual assistant. How can I help", 1) \
                if "How can I help" in greet else f"{greet} I'm a virtual assistant."
        if not self.is_open_now():
            if s.after_hours_behavior == "closed_message":
                pre.append("Our office is currently closed.")
            else:
                pre.append("Our office is currently closed, but I can still help you book an appointment or take a message.")
        consent = [pre.pop(0)] if s.recording_enabled and s.recording_consent and pre else []
        text = " ".join(consent + [greet] + pre)
        return self._say(text)

    def handle(self, text: str, confidence: float = 1.0) -> Reply:
        text = (text or "").strip()
        if self.ended:
            return self._say("")
        if not text:
            return self._unclear()
        if len(text) > 500:
            text = text[:500]
        text = nlu.fix_mishearings(text)
        self._asked_again = False
        intents = nlu.detect_intents(text)
        self.intents_seen.extend(i for i in intents if i not in self.intents_seen)

        # 1. Safety first: emergencies interrupt every flow, immediately.
        if nlu.is_emergency(text, self.snap.health):
            return self._emergency(text)
        if "injection" in intents:
            return self._say(f"I can only help with questions about {self.snap.name} and with appointments. "
                             "What can I help you with?")
        if self.snap.health and nlu.wants_medical_advice(text) and self.state in ("idle",):
            return self._say("I'm not able to give medical advice, but I can book you an appointment or ask a member "
                             "of our team to call you back. Which would you prefer?")
        hello = "Hi!" if nlu.is_greeting(text) else "Yes, I can hear you." if nlu.is_hearing_check(text) else ""
        if hello:
            return self._say(f"{hello} How can I help you today?" if self.state == "idle" else f"{hello} {self._reprompt()}")
        if "repeat" in intents and self.last_reply:
            return Reply(self.last_reply, state=self.state)
        if "booking_status" in intents:
            return self._booking_status()
        if "human" in intents and self.state not in ("msg_name", "msg_name_confirm", "msg_phone", "msg_reason"):
            return self._human()
        if self.flow == "message" and self.resume_service and "book" not in intents \
                and self.state in ("msg_name", "msg_name_confirm", "msg_phone", "msg_reason"):
            # The booking was given up on, but the caller is now giving a day or time: go back to it.
            w = nlu.parse_when(text, self.today)
            if w.explicit_date or w.time is not None or w.part_of_day:
                self.flow = "book"
                self.b = Booking(service_id=self.resume_service, when=w, name=self.caller_name, phone=self.caller_phone)
                self.resume_service = None
                return self._offer_slots("Okay, let's go back to your booking. ")
        if "goodbye" in intents and not ({"book", "reschedule", "cancel"} & set(intents)):
            if self.state in ("idle", "anything_else") or nlu.has(nlu.norm(text), nlu.END_CALL):
                return self._goodbye()
        if self.flow == "book":
            # The caller can change their mind in the middle of a booking.
            if "check_appointment" in intents:
                return self._start_lookup("check")
            if "reschedule" in intents:
                return self._start_lookup("reschedule")
            if "cancel" in intents and nlu.has(nlu.norm(text), r"\bmy (appointment|booking)\b"):
                return self._start_lookup("cancel")
            if self.state in LATE_BOOKING_STATES and self.b.service_id and nlu.wants_time_change(text, self.today):
                # "Can you make it 4 PM instead?" while giving a name or number: offer times again, keep what's known.
                self.b.when = nlu.parse_when(text, self.today)
                self.b.slot = None
                return self._offer_slots("Sure. ")
        if self.flow == "message" and "book" in intents and self.b.reason != "Urgent concern":
            # The caller was leaving a message but would rather book: do that, keeping what they've already said.
            if self.state == "book_phone_confirm" and nlu.is_affirm(text) and self.b.phone:
                self.caller_phone = self.b.phone
            return self._start_booking(text, default_service=self.resume_service)

        # 2. State-specific handling
        handler = getattr(self, f"_st_{self.state}", None)
        if handler is not None and self.state != "idle":
            misses = self.misunderstood
            reply = handler(text, intents, confidence)
            if reply is not None:
                if self.misunderstood == misses:  # otherwise the handler is asking again, which still counts
                    self.misunderstood = 0
                elif len(text.split()) >= 5:
                    # The step is asking again after a long sentence it couldn't use. The optional language helper can
                    # still tell what was meant (a question, a request for a person) and the step's question returns.
                    rescued = self._llm_rescue(text)
                    if rescued is not None:
                        self.misunderstood = misses + (1 if self._asked_again else 0)
                        return rescued
                return reply
            # Side questions mid-flow: answer and return to the pending question.
            side = self._answer_info(text, intents)
            if side:
                self.misunderstood = 0
                return self._say(f"{side} {self._reprompt()}")
            rescued = self._llm_rescue(text)
            if rescued is not None:
                return rescued
            return self._unclear(confidence)

        # 3. Top level
        reply = self._top(text, intents)
        if reply is not None:
            if not self._asked_again:
                self.misunderstood = 0
            return reply
        return self._unclear(confidence)

    # ------------------------------------------------------------------ top level
    def _top(self, text: str, intents: list[str]) -> Reply | None:
        if self.state == "anything_else" and nlu.is_deny(text) and not intents:
            return self._goodbye()
        if "book" in intents:
            if self.agent is not None and len(text.split()) >= 8:
                # A long booking sentence often carries the service, day, name and number at once: let the agent pick them up.
                r = self._agent_decide(text)
                if r is not None and r.action in ("book", "change_time"):
                    return self._agent_book(text, r)
            return self._start_booking(text)
        if "reschedule" in intents:
            return self._start_lookup("reschedule")
        if "cancel" in intents:
            return self._start_lookup("cancel")
        if "check_appointment" in intents:
            return self._start_lookup("check")
        info = self._answer_info(text, intents)
        if info:
            self.state = "anything_else"
            return self._say(f"{info} Is there anything else I can help with?")
        if "thanks" in intents:
            self.state = "anything_else"
            return self._say("You're welcome! Is there anything else I can help with?")
        if self.state == "anything_else" and nlu.is_affirm(text):
            self.state = "idle"
            return self._say("Sure, what can I help you with?")
        # The rules didn't understand: ask the agent (or the simpler optional LLM) what was meant
        if self.agent is not None:
            r = self._agent_decide(text)
            reply = self._agent_act(text, r, resume=False) if r is not None else None
            if reply is not None:
                return reply
        choice = None if self.agent is not None else self._llm_choice(text)
        if choice:
            intent, kb_id = choice
            if intent == "book":
                return self._start_booking(text)
            if intent in ("reschedule", "cancel"):
                return self._start_lookup(intent)
            if intent == "human":
                return self._human()
            if intent == "status":
                return self._booking_status()
            if intent == "kb" and kb_id:
                kb = next((k for k in self.snap.kb if k["id"] == kb_id), None)
                if kb:
                    self.state = "anything_else"
                    return self._say(f"{self._kb_say(kb, text)} Is there anything else I can help with?")
            if intent in ("hours", "location", "services", "price"):
                info = self._answer_info(text, [intent])
                if info:
                    self.state = "anything_else"
                    return self._say(f"{info} Is there anything else I can help with?")
        return None

    # ------------------------------------------------------------------ the agent
    def _agent_decide(self, text: str):
        """Ask the agent what an unclear sentence meant. Capped per call (the same cap as the simple LLM), never for
        one-word sentences, and any failure means "no decision": the rules carry on."""
        if self.agent is None or self.llm_calls >= self.llm_max_calls or len(text.split()) < 2:
            return None
        self.llm_calls += 1
        known = {"service": bool(self.b.service_id), "day_or_time": self.b.when is not None,
                 "name": bool(self.b.name or self.caller_name), "phone": bool(self.b.phone or self.caller_phone)}
        return self.agent.decide(text, {"state": self.state, "asking": self.last_reply, "today": self.today,
                                        "services": self.snap.services, "kb": self.snap.kb, "known": known})

    def _answer_then(self, info: str, resume: bool) -> Reply:
        """Say a saved answer, then go back to what was being asked (or offer more help, when nothing was pending)."""
        if resume:
            self.misunderstood = 0
            return self._say(f"{info} {self._reprompt()}")
        self.state = "anything_else"
        return self._say(f"{info} Is there anything else I can help with?")

    def _agent_act(self, text: str, r, resume: bool) -> Reply | None:
        """Carry out the agent's checked decision with the receptionist's own steps and the business's own words."""
        if r.action == "callback":
            return self._human()
        if r.action == "status":
            return self._booking_status()
        if r.action == "kb" and r.kb_id:
            kb = next((k for k in self.snap.kb if k["id"] == r.kb_id), None)
            return self._answer_then(self._kb_say(kb, text), resume) if kb else None
        if r.action == "info" and r.info:
            info = self._answer_info(text, [r.info])
            return self._answer_then(info, resume) if info else None
        if r.action == "clarify" and r.say:
            self.misunderstood += 1  # asking again still counts, so a caller can't be questioned forever
            self._asked_again = True
            return self._say(r.say)
        if r.action in ("book", "change_time"):
            return self._agent_book(text, r)
        return None

    def _agent_book(self, text: str, r) -> Reply:
        """The caller wants to book (or change a time) and may have given details in the same breath."""
        if r.name:
            self.caller_name = r.name
        if r.phone:
            self.caller_phone, self.phone_confirmed = r.phone, False
        when = nlu.parse_when(r.when_text, self.today) if r.when_text else None
        if self.flow == "book" and (self.b.service_id or r.service_id):
            # A booking is already under way: update it and carry on from the right step.
            if r.service_id:
                self.b.service_id = r.service_id
            if r.email:
                self.b.email = r.email
            if when is not None and not when.empty:
                self.b.when, self.b.slot = when, None
                return self._offer_slots("Sure. ")
            if self.state == "book_name" and r.name:
                self.b.name = r.name
                return self._ask_phone()
            if self.state == "book_phone" and r.phone:
                self.b.phone = r.phone
                self.state = "book_phone_confirm"
                return self._say(f"I have {self._phone_readback(r.phone)}. Is that correct?")
            return self._reprompt_reply()
        reply = self._start_booking(f"{text} {r.when_text or ''}".strip(), default_service=r.service_id)
        if r.email:
            self.b.email = r.email
        return reply

    def _reprompt_reply(self) -> Reply:
        return self._say(self._reprompt())

    def _llm_choice(self, text: str) -> tuple[str, str | None] | None:
        """Ask the optional LLM what an unclear sentence meant. Capped per call, and skipped for very short sentences,
        so the cost stays small however long the call is."""
        if self.llm is None or self.llm_calls >= self.llm_max_calls or len(text.split()) < 2:
            return None
        self.llm_calls += 1
        return self.llm.classify(text, self.snap, self.last_reply)

    def _llm_rescue(self, text: str) -> Reply | None:
        """A sentence none of the step's rules understood, in the middle of a conversation. The LLM can still tell if
        it was a question we can answer or a request for a person, and we then return to the pending question."""
        if self.agent is not None:
            r = self._agent_decide(text)
            return self._agent_act(text, r, resume=True) if r is not None else None
        choice = self._llm_choice(text)
        if not choice:
            return None
        intent, kb_id = choice
        if intent == "human":
            return self._human()
        if intent == "status":
            return self._booking_status()
        info = None
        if intent == "kb" and kb_id:
            kb = next((k for k in self.snap.kb if k["id"] == kb_id), None)
            info = self._kb_say(kb, text) if kb else None
        elif intent in ("hours", "location", "services", "price"):
            info = self._answer_info(text, [intent])
        if info:
            self.misunderstood = 0
            return self._say(f"{info} {self._reprompt()}")
        return None

    def _answer_info(self, text: str, intents: list[str]) -> str | None:
        """Answers strictly from configured business data. Returns None if nothing applies."""
        t = nlu.norm(text)
        kb = self._kb_match(text)
        if "hours" in intents:
            when = nlu.parse_when(text, self.today)
            if when.explicit_date and when.dates:
                d = when.dates[0]
                iv = self._hours_for(d)
                label = spoken_date(d, self.today)
                hol = self.snap.holidays.get(d)
                note = f" for {hol[0]}" if hol and hol[0] else ""
                return (f"On {label} we're closed{note}." if not iv else f"On {label} we're open {self._fmt_intervals(iv)}.")
            if nlu.has(t, r"\b(right now|now|currently|still open)\b"):
                return "Yes, we're open right now." if self.is_open_now() else "We're closed right now. " + self.hours_summary()
            return self.hours_summary()
        if "location" in intents:
            if kb and kb[1] >= 0.6 and nlu.has(t, r"\bpark"):
                return self._kb_say(kb[0], text)
            if self.snap.address:
                extra = f" {self.snap.directions.strip()}" if self.snap.directions.strip() and nlu.has(t, r"direction|find|get there|park") else ""
                return f"We're located at {self.snap.address}.{extra}"
            return "I don't have our address on file, but I can have someone from our team call you with directions."
        if "price" in intents:
            sid = nlu.match_service(text, [(s["id"], s["name"]) for s in self.snap.services])
            if kb and kb[1] >= 0.6:
                return self._kb_say(kb[0], text)
            if sid:
                s = self._service(sid)
                if s and s["price"]:
                    return f"A {s['name'].lower()} is {s['price']}."
                return (f"I don't have pricing for {s['name'].lower()} on hand. I can ask someone from our team to follow "
                        "up with you about that.")
            priced = [s for s in self.snap.services if s["price"]]
            if priced:
                return "Here's what I have: " + "; ".join(f"{s['name']}, {s['price']}" for s in priced[:4]) + "."
            return "I don't have pricing information available, but a team member can follow up with you."
        if "services" in intents:
            if kb and kb[1] >= 0.75:
                return self._kb_say(kb[0], text)
            names = [s["name"] for s in self.snap.services]
            if not names:
                return None
            listed = ", ".join(names[:5]) if len(names) <= 2 else ", ".join(names[:4]) + (", and " + names[4] if len(names) > 4 else "")
            if 2 < len(names) <= 4:
                listed = ", ".join(names[:-1]) + ", and " + names[-1]
            return f"We offer {listed}."
        if kb and kb[1] >= 0.5:
            return self._kb_say(kb[0], text)
        return None

    @staticmethod
    def _kb_say(entry: dict, text: str) -> str:
        return nlu.best_sentences(entry["a"], text)

    def _kb_match(self, text: str) -> tuple[dict, float] | None:
        best, score = None, 0.0
        for k in self.snap.kb:
            s = nlu.kb_score(text, k["q"], k["a"])
            if s > score:
                best, score = k, s
        return (best, score) if best else None

    # ------------------------------------------------------------------ booking
    def _booking_status(self) -> Reply:
        """"Did you book it?" Answer plainly from what actually happened on this call."""
        booked = [a for a in self.actions if a.startswith("booked ")]
        if booked:
            self.state = "anything_else"
            return self._say(f"Yes, your {booked[-1][len('booked '):]} is booked. Is there anything else I can help with?")
        self.state = "offer_booking"
        passed = "I've passed your details to our team and they'll call you back. " if "callback" in self.outcome_flags else ""
        return self._say(f"I haven't booked an appointment yet. {passed}Would you like me to try to book a time now?")

    def _st_offer_booking(self, text, intents, conf):
        if "book" in intents or nlu.is_affirm(text):
            return self._start_booking(text if "book" in intents else "book", default_service=self.resume_service)
        if nlu.is_deny(text):
            self.state = "anything_else"
            return self._say("No problem. Is there anything else I can help with?")
        return None

    def _start_booking(self, text: str, default_service: str | None = None) -> Reply:
        self.flow = "book"
        self.b = Booking()
        self.b.reason = text
        self.actions.append("asked to book")
        services = [(s["id"], s["name"]) for s in self.snap.services]
        if not services:
            self.state = "anything_else"
            return self._say("I'm sorry, online booking isn't set up yet. I can ask someone from our team to call you back. "
                             "Would you like that?")
        self.b.service_id = nlu.match_service(text, services) or default_service or (services[0][0] if len(services) == 1 else None)
        when = nlu.parse_when(text, self.today)
        self.b.when = None if when.empty else when
        if not self.b.service_id:
            self.state = "book_service"
            return self._say(f"Of course! I can help with that. What would you like to come in for? {self._service_menu()}")
        if self.b.when is None:
            self.state = "book_when"
            service = self._service(self.b.service_id)
            what = f"a {service['name'].lower()}" if service and len(services) > 1 else "that"
            return self._say(f"Of course! I can help with {what}. Do you have a preferred day or time?")
        return self._offer_slots()

    def _service_menu(self) -> str:
        names = [s["name"] for s in self.snap.services][:4]
        if len(names) == 1:
            return f"We offer {names[0]}."
        return "For example, " + ", ".join(names[:-1]) + f", or {names[-1]}."

    def _st_book_service(self, text, intents, conf):
        sid = nlu.match_service(text, [(s["id"], s["name"]) for s in self.snap.services])
        if not sid:
            return None if intents else self._retry(f"Sorry, I didn't catch which service. {self._service_menu()}")
        self.b.service_id = sid
        when = nlu.parse_when(text, self.today)
        if not when.empty:
            self.b.when = when
            return self._offer_slots()
        self.state = "book_when"
        return self._say(f"Great, a {self._service_name(sid)}. Do you have a preferred day or time?")

    def _st_book_when(self, text, intents, conf):
        when = nlu.parse_when(text, self.today)
        if when.empty:
            if nlu.match_service(text, [(s["id"], s["name"]) for s in self.snap.services]):
                # They said what they want again instead of a day: just offer the next openings.
                when.flexible = True
                self.b.when = when
                return self._offer_slots("Sure. ")
            if nlu.is_deny(text) or nlu.has(nlu.norm(text), r"\b(no preference|not really)\b"):
                when.flexible = True
            elif nlu.is_affirm(text):
                return self._say("Great, what day and time would you like?")
            else:
                return None
        self.b.when = when
        return self._offer_slots()

    def _offer_slots(self, prefix: str = "") -> Reply:
        assert self.b.service_id
        w = self.b.when or nlu.When(flexible=True)
        exclude = self.b.appointment.id if self.flow == "reschedule" and self.b.appointment else None
        opts = self.tools.find_slots(self.b.service_id, w, exclude_appointment_id=exclude)
        self.actions.append("checked availability")
        if not opts and (w.time or w.part_of_day):
            # Relax time constraints but keep the day.
            opts = self.tools.find_slots(self.b.service_id, nlu.When(dates=w.dates), exclude_appointment_id=exclude)
            if opts:
                prefix += "I don't have that exact time. "
        if not opts and w.dates:
            label = spoken_date(w.dates[0], self.today) if len(w.dates) == 1 else "then"
            opts = self.tools.find_slots(self.b.service_id, nlu.When(flexible=True, dates=[d for d in (w.dates[-1] + timedelta(days=i) for i in range(1, 15))]),
                                         exclude_appointment_id=exclude)
            if opts:
                prefix += f"I'm sorry, we don't have any openings {label if len(w.dates) == 1 else 'in that time frame'}. "
        if not opts:
            self.state = "anything_else"
            self.outcome_flags.add("no_availability")
            return self._say(prefix + "I'm sorry, I don't see any available times in the next few weeks. "
                             "I can ask our team to call you back to find a time. Would you like that?")
        self.b.options = opts
        self.state = f"{self.flow}_pick"
        self.relists = 0
        if len(opts) == 1:
            return self._say(f"{prefix}I have {self._when_text(opts[0].start)} available. Would that work for you?")
        return self._say(f"{prefix}{self._options_text()} Which works best for you?")

    def _short_day(self, dt: datetime) -> str:
        """A day as briefly as it can be said without being ambiguous: "tomorrow", "Monday", "Monday the 16th"."""
        d = dt.astimezone(self.tz).date()
        if (d - self.today).days <= 1:
            return spoken_date(d, self.today)
        if (d - self.today).days < 7:
            return DAY_NAMES[d.weekday()]
        suffix = "th" if 11 <= d.day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
        return f"{DAY_NAMES[d.weekday()]} the {d.day}{suffix}"

    def _options_text(self) -> str:
        """The offered times, kept short: long lists are hard to follow on the phone."""
        opts = self.b.options
        local = [o.start.astimezone(self.tz) for o in opts]
        if len({x.date() for x in local}) == 1:
            day = self._short_day(opts[0].start)
            times = [spoken_time(x) for x in local]
            return f"{day[0].upper() + day[1:]}, I have {', '.join(times[:-1])}, or {times[-1]}."
        if len({x.time() for x in local}) == 1:
            days = [self._short_day(o.start) for o in opts]
            return f"I have {spoken_time(local[0])} {', '.join(days[:-1])}, or {days[-1]}."
        listed = [f"{self._short_day(o.start)} at {spoken_time(x)}" for o, x in zip(opts, local)]
        return f"The next openings are {', '.join(listed[:-1])}, or {listed[-1]}."

    def _pick(self, text: str) -> SlotOption | None | str:
        opts = self.b.options
        idx = nlu.parse_choice(text, len(opts))
        if idx is not None:
            return opts[idx]
        t = nlu.parse_time(text)
        w = nlu.parse_when(text, self.today)
        if t is not None:
            matches = [o for o in opts if o.start.astimezone(self.tz).time() == t and
                       (not w.explicit_date or o.start.astimezone(self.tz).date() in w.dates)]
            if len(matches) == 1:
                return matches[0]
            return "search"  # a time we did not offer: search for it
        if w.explicit_date:
            matches = [o for o in opts if o.start.astimezone(self.tz).date() in w.dates]
            if len(matches) == 1:
                return matches[0]
            return "search"
        if len(opts) == 1 and nlu.is_affirm(text):
            return opts[0]
        if nlu.has(nlu.norm(text), r"\b(earlier|later|another|other|different|none|neither|something else)\b") or nlu.is_deny(text):
            return "other"
        return None

    def _st_book_pick(self, text, intents, conf):
        choice = self._pick(text)
        if choice is None:
            if ("book" in intents or nlu.is_affirm(text)) and len(self.b.options) > 1 and self.relists < 2:
                # They want one of the times but didn't say which (or it wasn't heard): offer them again, plainly.
                self.relists += 1
                return self._say(f"Sure. {self._options_text()} Which one would you like?")
            return None
        if choice == "other" or choice == "search":
            w = nlu.parse_when(text, self.today)
            if choice == "search" and not w.empty:
                if not w.dates and self.b.when and self.b.when.dates:
                    w.dates = self.b.when.dates
                self.b.when = w
                return self._offer_slots()
            self.state = f"{self.flow}_when"
            return self._say("No problem. What other day or time would work for you?")
        self.b.slot = choice
        if self.flow == "reschedule":
            return self._confirm_reschedule()
        self.state = "book_name"
        if self.caller_name:
            self.b.name = self.caller_name
            return self._ask_phone()
        return self._say(f"Great, {self._when_text(choice.start)}. May I have your full name, please?")

    def _st_book_name(self, text, intents, conf):
        name = nlu.extract_name(text, expecting=True)
        if not name:
            return None if intents else self._retry("Sorry, I didn't catch your name. Could you say it again?")
        self.b.name = name
        self.caller_name = name
        if conf < NAME_SURE:
            self.state = "book_name_confirm"
            return self._say(f"I have your name as {name}. Is that right?")
        return self._ask_phone()

    def _st_book_name_confirm(self, text, intents, conf):
        next_step = self._msg_ask_phone if self.flow == "message" else self._ask_phone
        if nlu.is_affirm(text):
            return next_step()
        name = nlu.extract_name(text, expecting=not nlu.is_deny(text))
        if name and nlu.norm(name) not in ("no", "nope"):
            self.b.name = self.caller_name = name
            return next_step()
        self.state = "msg_name" if self.flow == "message" else "book_name"
        return self._say("Sorry about that. Could you tell me your full name again?")

    _st_msg_name_confirm = _st_book_name_confirm

    def _ask_phone(self) -> Reply:
        if self.caller_phone and self.b.phone is None:
            self.b.phone = self.caller_phone
            if not self.phone_confirmed:
                # Picked up from a long sentence: read it back before relying on it.
                self.state = "book_phone_confirm"
                return self._say(f"I have {self._phone_readback(self.caller_phone)}. Is that correct?")
            return self._confirm_booking()
        if self.caller_number and self.b.phone is None:
            self.state = "book_phone_callerid"
            return self._say(f"Thanks, {self.b.name.split()[0]}. Is the number you're calling from, ending in "
                             f"{self._spell(self.caller_number[-4:])}, the best number to reach you?")
        self.state = "book_phone"
        return self._say(f"Thanks, {self.b.name.split()[0]}. What's the best phone number to reach you?")

    @staticmethod
    def _spell(digits: str) -> str:
        return " ".join(digits)

    def _phone_readback(self, e164: str) -> str:
        d = e164[2:] if e164.startswith("+1") and len(e164) == 12 else e164.lstrip("+")
        if len(d) == 10:
            return f"{self._spell(d[:3])}, {self._spell(d[3:6])}, {self._spell(d[6:])}"
        if len(d) == 11:
            return f"{self._spell(d[:4])}, {self._spell(d[4:7])}, {self._spell(d[7:])}"
        return self._spell(d)

    def _take_phone(self, text: str) -> Reply | None:
        """Handle one turn of a caller giving their number. People often say it in pieces and pause between groups of
        digits, so it's collected across turns. Returns None if this turn wasn't about the number."""
        said = nlu.phone_digits(text)
        if self.flow in ("book", "message") and not said:
            # No phone number to give: an e-mail address will do for a booking or a message.
            if nlu.extract_email(text):
                return self._take_email(text)
            if nlu.says_no_phone(text):
                return self._ask_email()
        digits = said if nlu.phone_from_digits(said) else self.b.partial_digits + said
        phone = nlu.phone_from_digits(digits)
        if phone:
            self.b.partial_digits = ""
            self.b.phone = phone
            self.state = "book_phone_confirm"
            return self._say(f"I have {self._phone_readback(phone)}. Is that correct?")
        if len(digits) > 15:
            self.b.partial_digits = ""
            return self._retry("Sorry, I didn't get that number. Could you say it again, slowly?")
        if digits:
            self.b.partial_digits = digits
            return self._say("Okay, go on.")
        if nlu.has(nlu.norm(text), r"\b(number|phone)\b"):
            return self._say("Sure, go ahead.")
        return None

    # ------------------------------------------------------------------ e-mail instead of a phone number
    def _ask_email(self) -> Reply:
        self.b.partial_digits = ""
        self.state = "book_email"
        return self._say("No problem. What's the best email address to reach you?")

    @staticmethod
    def _spoken_email(email: str) -> str:
        user, domain = email.split("@", 1)
        spelled = " dot ".join(" ".join(part) for part in user.split("."))  # letters one by one, "dot" as a word
        return f"{spelled} at {domain.replace('.', ' dot ')}"

    def _take_email(self, text: str) -> Reply | None:
        email = nlu.extract_email(text)
        if not email:
            return None
        self.b.email = email
        self.state = "book_email_confirm"
        return self._say(f"I have {self._spoken_email(email)}. Is that correct?")

    def _after_contact(self) -> Reply:
        """The caller's way of being reached is settled (a phone number or an e-mail): carry on."""
        if self.flow == "message":
            return self._st_msg_finish()
        return self._confirm_booking()

    def _st_book_email(self, text, intents, conf):
        reply = self._take_email(text)
        if reply is not None:
            return reply
        if nlu.is_deny(text):  # nothing to give: the booking still goes ahead
            self.b.email = ""
            return self._after_contact()
        return self._retry("Sorry, could you say your email address again?")

    def _st_book_email_confirm(self, text, intents, conf):
        if nlu.is_affirm(text):
            return self._after_contact()
        again = self._take_email(text)
        if again is not None:
            return again
        if nlu.is_deny(text):
            self.state = "book_email"
            return self._say("Sorry about that. Could you say the email address again, slowly?")
        return None

    _st_msg_email = _st_book_email

    def _st_book_phone_callerid(self, text, intents, conf):
        if nlu.is_affirm(text):
            self.b.phone = self.caller_phone = self.caller_number
            self.phone_confirmed = True
            return self._confirm_booking()
        phone = nlu.extract_phone(text)
        if phone:
            self.b.phone = phone
            self.state = "book_phone_confirm"
            return self._say(f"Thanks. I have {self._phone_readback(phone)}. Is that correct?")
        if nlu.is_deny(text):
            self.state = "book_phone"
            return self._say("No problem. What number should we use?")
        return None

    def _st_book_phone(self, text, intents, conf):
        reply = self._take_phone(text)
        if reply is not None:
            return reply
        if nlu.NAME_PREFIX.search(text) and (name := nlu.extract_name(text)):
            self.b.name = self.caller_name = name
            return self._say(f"Thanks, {name}. And what's the best phone number to reach you?")
        return None

    def _st_book_phone_confirm(self, text, intents, conf):
        if nlu.is_affirm(text):
            self.caller_phone, self.phone_confirmed = self.b.phone, True
            if self.flow == "message":
                return self._st_msg_finish()
            if self.flow in ("reschedule", "cancel", "check"):
                return self._lookup_by_phone()
            return self._confirm_booking()
        phone = nlu.extract_phone(text)
        if phone:
            self.b.phone = phone
            return self._say(f"Thanks. I have {self._phone_readback(phone)}. Is that correct?")
        ask_again = "msg_phone" if self.flow == "message" else ("lookup_phone" if self.flow in ("reschedule", "cancel", "check") else "book_phone")
        if nlu.is_deny(text):
            self.b.phone = None
            self.state = ask_again
            return self._say("Sorry about that. Could you say the number again, digit by digit?")
        if nlu.phone_digits(text):
            # Part of a number, like "9 double seven": a piece can't be fitted into the one read back, so start it over.
            self.b.phone = None
            self.state = ask_again
            return self._retry("Sorry, I didn't catch the whole number. Could you say all of it again, digit by digit?")
        return None

    def _confirm_booking(self) -> Reply:
        assert self.b.slot and self.b.name
        self.state = "book_confirm"
        staff = f" with {self.b.slot.staff_name}" if self.b.slot.staff_name else ""
        return self._say(f"Let me confirm: a {self._service_name(self.b.service_id)} {self._when_text(self.b.slot.start)}"
                         f"{staff}, for {self.b.name}. Shall I book that for you?")

    def _st_book_confirm(self, text, intents, conf):
        if nlu.is_affirm(text):
            extra = {"email": self.b.email} if self.b.email else {}
            res = self.tools.book(self.b.service_id, self.b.slot, self.b.name, self.b.phone, **extra)
            if res.ok and res.appointment:
                # Confirmation only after the backend committed the booking.
                self.actions.append(f"booked {self._service_name(self.b.service_id)} {self._when_text(res.appointment.start)}")
                self.outcome_flags.add("booked")
                self.state = "anything_else"
                self.flow = ""
                return self._say(f"You're all set! Your {self._service_name(self.b.service_id)} is booked for "
                                 f"{self._when_text(res.appointment.start)}. Is there anything else I can help with?")
            if res.code == "slot_unavailable":
                self.actions.append("slot was taken during booking")
                self.b.slot = None
                return self._offer_slots("I'm sorry, that time was just taken. ")
            self.outcome_flags.add("tool_error")
            self.state = "anything_else"
            return self._say("I'm sorry, I wasn't able to complete the booking. I'll ask our team to call you back to "
                             "finish it. Is there anything else I can help with?") if self._callback_silent("Booking failed") else \
                self._say("I'm sorry, something went wrong while booking. Please call back shortly.")
        if nlu.is_deny(text):
            self.state = "book_change"
            return self._say("No problem. What would you like to change: the day and time, or your details?")
        return None

    def _st_book_change(self, text, intents, conf):
        t = nlu.norm(text)
        if nlu.has(t, r"\b(time|day|date|when|earlier|later)\b") or not nlu.parse_when(text, self.today).empty:
            w = nlu.parse_when(text, self.today)
            if not w.empty:
                self.b.when = w
                return self._offer_slots()
            self.state = "book_when"
            return self._say("Sure. What day or time would you prefer?")
        if nlu.has(t, r"\b(name)\b"):
            self.state = "book_name"
            return self._say("Of course. What's the correct name?")
        if nlu.has(t, r"\b(number|phone)\b"):
            self.state = "book_phone"
            return self._say("Of course. What's the best number to reach you?")
        if nlu.has(t, r"\b(service|treatment|type)\b"):
            self.state = "book_service"
            return self._say(f"Sure. What would you like to book? {self._service_menu()}")
        if nlu.has(t, r"\b(nothing|never mind|nevermind|forget it|cancel)\b"):
            self.state = "anything_else"
            self.flow = ""
            return self._say("Okay, I won't book anything. Is there anything else I can help with?")
        return None

    def _callback_silent(self, reason: str) -> bool:
        try:
            self.tools.request_callback(self.b.name or "", self.b.phone or self.caller_number, reason)
            self.outcome_flags.add("callback")
            return True
        except Exception:
            log.exception("callback request failed")
            return False

    # ------------------------------------------------------------------ reschedule / cancel / check
    def _start_lookup(self, flow: str) -> Reply:
        self.flow = flow
        self.b = Booking()
        self.actions.append({"reschedule": "asked to reschedule", "cancel": "asked to cancel", "check": "asked about their appointment"}[flow])
        if self.caller_phone or self.caller_number:
            self.b.phone = self.caller_phone or self.caller_number
            return self._lookup_by_phone()
        self.state = "lookup_phone"
        return self._say("I can help with that. What's the phone number the appointment was booked under?")

    def _st_lookup_phone(self, text, intents, conf):
        return self._take_phone(text)

    def _lookup_by_phone(self) -> Reply:
        appts = self.tools.find_appointments(self.b.phone)
        self.actions.append("looked up appointments")
        if not appts:
            self.state = "anything_else"
            first = "I couldn't find any upcoming appointments under that number."
            if self.flow == "check":
                return self._say(f"{first} Would you like to book one?")
            return self._say(f"{first} I can ask our team to call you back to sort it out. Would you like that?")
        self.b.candidates = appts
        if len(appts) == 1:
            return self._chose_appt(appts[0])
        self.state = "lookup_pick"
        listed = ", ".join(f"a {a.service_name.lower()} {self._when_text(a.start)}" for a in appts[:3])
        return self._say(f"I found {len(appts)} upcoming appointments: {listed}. Which one do you mean?")

    def _st_lookup_pick(self, text, intents, conf):
        opts = self.b.candidates[:3]
        idx = nlu.parse_choice(text, len(opts))
        if idx is None:
            sid = nlu.match_service(text, [(a.id, a.service_name) for a in opts])
            w = nlu.parse_when(text, self.today)
            if sid:
                return self._chose_appt(next(a for a in opts if a.id == sid))
            if w.explicit_date:
                m = [a for a in opts if a.start.astimezone(self.tz).date() in w.dates]
                if len(m) == 1:
                    return self._chose_appt(m[0])
            return None
        return self._chose_appt(opts[idx])

    def _chose_appt(self, a: ApptInfo) -> Reply:
        self.b.appointment = a
        self.b.service_id = a.service_id
        desc = f"your {a.service_name.lower()} {self._when_text(a.start)}"
        if self.flow == "check":
            self.state = "anything_else"
            return self._say(f"You have {desc}. Is there anything else I can help with?")
        if self.flow == "cancel":
            self.state = "cancel_confirm"
            policy = f" {self.snap.cancellation_policy}" if self.snap.cancellation_policy else ""
            return self._say(f"I found {desc}.{policy} Would you like me to cancel it?")
        self.state = "reschedule_when"
        return self._say(f"I found {desc}. What day or time would you like to move it to?")

    def _st_cancel_confirm(self, text, intents, conf):
        if nlu.is_affirm(text):
            res = self.tools.cancel(self.b.appointment.id)
            if res.ok:
                self.actions.append(f"cancelled {self.b.appointment.service_name.lower()} {self._when_text(self.b.appointment.start)}")
                self.outcome_flags.add("cancelled")
                self.state = "anything_else"
                return self._say("Your appointment has been cancelled. Would you like to book a new time?")
            if res.code == "policy_notice":
                self._callback_silent(f"Wants to cancel appointment inside notice period: {res.message}")
                self.state = "anything_else"
                return self._say(f"I'm sorry, I can't cancel that one automatically. {res.message} I've asked our team to "
                                 "call you back about it. Is there anything else I can help with?")
            self.state = "anything_else"
            return self._say("I'm sorry, I couldn't cancel that appointment right now. I'll have someone call you back.")
        if nlu.is_deny(text):
            self.state = "anything_else"
            return self._say("Okay, I'll keep your appointment as it is. Is there anything else I can help with?")
        return None

    def _st_reschedule_when(self, text, intents, conf):
        w = nlu.parse_when(text, self.today)
        if w.empty:
            return None
        self.b.when = w
        return self._offer_slots()

    _st_reschedule_pick = _st_book_pick

    def _confirm_reschedule(self) -> Reply:
        self.state = "reschedule_confirm"
        return self._say(f"Just to confirm, you'd like to move your {self.b.appointment.service_name.lower()} to "
                         f"{self._when_text(self.b.slot.start)}. Is that right?")

    def _st_reschedule_confirm(self, text, intents, conf):
        if nlu.is_affirm(text):
            res = self.tools.reschedule(self.b.appointment.id, self.b.slot)
            if res.ok and res.appointment:
                self.actions.append(f"rescheduled to {self._when_text(res.appointment.start)}")
                self.outcome_flags.add("rescheduled")
                self.state = "anything_else"
                return self._say(f"Done! Your appointment is now {self._when_text(res.appointment.start)}. "
                                 "Is there anything else I can help with?")
            if res.code == "slot_unavailable":
                return self._offer_slots("I'm sorry, that time was just taken. ")
            if res.code == "policy_notice":
                self._callback_silent(f"Wants to reschedule inside notice period: {res.message}")
                self.state = "anything_else"
                return self._say(f"I'm sorry, I can't move that one automatically. {res.message} I've asked our team to "
                                 "call you back. Is there anything else I can help with?")
            self.state = "anything_else"
            return self._say("I'm sorry, I couldn't change that appointment. I'll have someone call you back.")
        if nlu.is_deny(text):
            self.state = "reschedule_when"
            return self._say("No problem. What other day or time would work?")
        return None

    # ------------------------------------------------------------------ messages, humans, emergencies
    def _can_transfer(self) -> bool:
        return bool(self.channel == "phone" and self.snap.transfer_number and self.is_open_now())

    def _human(self) -> Reply:
        self.actions.append("asked for a person")
        if self._can_transfer():
            self.wants_transfer = True
            self.outcome_flags.add("transferred")
            self.ended = True
            return self._say("Of course. Please hold while I transfer you to a member of our team.", transfer=True, end_call=True)
        self.flow = "message"
        self.b = Booking(reason="Asked to speak with a person")
        if self.caller_name:
            self.b.name = self.caller_name
            return self._msg_ask_phone()
        self.state = "msg_name"
        return self._say("Of course. I'll have a member of our team call you back. May I have your name?")

    def _emergency(self, text: str) -> Reply:
        self.actions.append("reported an urgent concern")
        self.outcome_flags.add("urgent")
        phone = self.b.phone or self.caller_phone or self.caller_number
        self.tools.request_callback(self.caller_name or "", phone, f"Urgent concern reported by caller: {text[:200]}", urgent=True)
        policy = self.snap.emergency_policy.strip() or "If this is an emergency, please hang up and call your local emergency number now."
        self.flow = "message"
        self.b = Booking(reason="Urgent concern", name=self.caller_name, phone=phone)
        if phone:
            self.state = "anything_else"
            return self._say(f"{policy} I've also alerted our team so someone can call you back as soon as possible.")
        self.state = "msg_phone"
        return self._say(f"{policy} I've alerted our team. What's the best number for them to call you back?")

    def _st_msg_name(self, text, intents, conf):
        name = nlu.extract_name(text, expecting=True)
        if not name:
            return None if intents else self._retry("Sorry, could you tell me your name again?")
        self.b.name = self.caller_name = name
        if conf < NAME_SURE:
            self.state = "msg_name_confirm"
            return self._say(f"I have your name as {name}. Is that right?")
        return self._msg_ask_phone()

    def _msg_ask_phone(self) -> Reply:
        first_name = self.b.name.split()[0]
        if self.caller_phone and not self.b.phone:
            self.b.phone = self.caller_phone
        if self.b.phone or self.caller_number:
            self.b.phone = self.b.phone or self.caller_number
            where = "the number you're calling from" if self.b.phone == self.caller_number else "the number you gave me"
            if self.b.reason_final:
                return self._finish_message(f"Thanks, {first_name}. Our team will call you back at {where}.")
            self.state = "msg_reason"
            return self._say(f"Thanks, {first_name}. We'll call you back at {where}. "
                             "In a few words, what should we call you about?")
        self.state = "msg_phone"
        return self._say(f"Thanks, {first_name}. What's the best number to reach you?")

    def _st_msg_phone(self, text, intents, conf):
        reply = self._take_phone(text)
        if reply is not None:
            return reply
        if nlu.NAME_PREFIX.search(text) and (name := nlu.extract_name(text)):  # "No, my name is ..."
            self.b.name = self.caller_name = name
            return self._say(f"Thanks, {name}. And what's the best phone number to reach you?")
        return None

    def _st_msg_finish(self) -> Reply:
        if self.b.reason == "Urgent concern":
            self.tools.request_callback(self.b.name or "", self.b.phone, "Callback number for urgent concern", urgent=True)
            self.state = "anything_else"
            return self._say("Thank you. Our team will call you back as soon as possible. If things get worse, please "
                             "call your local emergency number.")
        if self.b.reason_final:
            return self._finish_message("Thank you. Our team will call you back.")
        self.state = "msg_reason"
        return self._say("Got it. In a few words, what should we call you about?")

    def _st_msg_reason(self, text, intents, conf):
        self.b.reason = f"Callback request: {text.strip()[:300]}"
        return self._finish_message("Thank you. I've passed your message to our team and someone will call you back.")

    def _finish_message(self, spoken: str) -> Reply:
        reason = self.b.reason + (f" Contact email: {self.b.email}." if self.b.email else "")
        self.tools.request_callback(self.b.name or "", self.b.phone, reason)
        self.actions.append("requested a callback")
        self.outcome_flags.add("callback")
        self.state = "anything_else"
        self.flow = ""
        self.resume_service = None  # the team has been asked to call back, so the booking isn't pending any more
        return self._say(f"{spoken} Is there anything else I can help with?")

    def _st_anything_else(self, text, intents, conf):
        if nlu.is_deny(text) and not intents:
            return self._goodbye()
        if nlu.is_affirm(text) and not intents:
            if "no_availability" in self.outcome_flags or self.flow in ("reschedule", "cancel"):
                if self.flow == "cancel" and "cancelled" in self.outcome_flags:
                    return self._start_booking("book")
                self.flow = "message"
                self.b = Booking(reason="Could not find a suitable time", name=self.caller_name, phone=self.b.phone)
                if self.b.name:
                    return self._msg_ask_phone()
                self.state = "msg_name"
                return self._say("Okay. May I have your name so our team can call you back?")
            if self.flow == "check":
                return self._start_booking("book")
            self.state = "idle"
            return self._say("Sure, what can I help you with?")
        return self._top(text, intents)

    def _goodbye(self) -> Reply:
        self.ended = True
        self.state = "ended"
        closing = self.snap.closing.strip() or f"Thanks for calling {self.snap.name}. Have a great day!"
        return self._say(closing, end_call=True)

    def _reprompt(self) -> str:
        return {
            "book_service": "Now, what would you like to book?",
            "book_when": "Now, what day or time works best for you?",
            "book_pick": "Which of those times works for you?",
            "reschedule_pick": "Which of those times works for you?",
            "book_name": "May I have your full name?",
            "book_name_confirm": "Did I get your name right?",
            "msg_name_confirm": "Did I get your name right?",
            "offer_booking": "Would you like me to try to book a time now?",
            "book_email": "What's the best email address to reach you?",
            "book_email_confirm": "Is that email address correct?",
            "book_phone": "What's the best phone number to reach you?",
            "book_phone_callerid": "Is the number you're calling from the best one to reach you?",
            "book_phone_confirm": "Is that number correct?",
            "book_confirm": "Shall I go ahead and book it?",
            "book_change": "What would you like to change?",
            "lookup_phone": "What's the phone number on the appointment?",
            "lookup_pick": "Which appointment do you mean?",
            "cancel_confirm": "Would you like me to cancel it?",
            "reschedule_when": "What day or time would you like instead?",
            "reschedule_confirm": "Shall I make that change?",
            "msg_name": "May I have your name?",
            "msg_phone": "What's the best number to reach you?",
            "msg_reason": "What should we call you about?",
        }.get(self.state, "Is there anything else I can help with?")

    def _retry(self, text: str) -> Reply:
        """Ask again. This counts as a misunderstanding, so a caller can't get stuck on one question forever."""
        self.misunderstood += 1
        return self._escalate() if self.misunderstood >= 3 else self._say(text)

    def _escalate(self) -> Reply:
        """Repeated misunderstandings: hand over to a person, and be plain about what has and hasn't happened."""
        self.misunderstood = 0
        if self.flow == "message":
            return self._give_up()
        if self.flow == "book" and self.state == "book_when" and self.b.service_id and not self.fallback_offered:
            # The caller can't seem to give a day: don't give up on the booking, offer the next openings.
            self.fallback_offered = True
            self.b.when = nlu.When(flexible=True)
            return self._offer_slots("Let me just offer the next openings. ")
        if self._can_transfer():
            return self._human()
        if self.flow == "book":
            self.resume_service = self.b.service_id
        spoken = "I'm sorry, I'm having trouble understanding. "
        spoken += "I haven't booked anything yet, so I'll ask our team to call you back." if self.flow == "book" else "I'll ask our team to call you back."
        reason = self._unfinished_request()
        name, phone, email = self.b.name or self.caller_name, self.b.phone or self.caller_phone or self.caller_number, self.b.email
        self.actions.append("could not be understood")
        self.flow = "message"
        self.b = Booking(reason=reason, reason_final=True, name=name, phone=phone, email=email)
        if not name:
            self.state = "msg_name"
            return self._say(f"{spoken} May I have your name?")
        if not phone:
            self.state = "msg_phone"
            return self._say(f"{spoken} What's the best number to reach you?")
        return self._finish_message(spoken)

    def _unfinished_request(self) -> str:
        """What the caller wanted, for the team calling them back."""
        if self.flow == "book":
            service = self._service(self.b.service_id)
            wanted = f"a {service['name'].lower()}" if service else "an appointment"
            when = f" {self._when_text(self.b.slot.start)}" if self.b.slot else ""
            return f"Wanted to book {wanted}{when}, but the receptionist couldn't understand them. Nothing was booked."
        if self.flow in ("reschedule", "cancel", "check"):
            return f"Wanted help with an appointment ({self.flow}), but the receptionist couldn't understand them."
        return "The receptionist couldn't understand them."

    def _give_up(self) -> Reply:
        """Even taking a message isn't working. End the call politely instead of asking the same thing forever."""
        self._callback_silent(self.b.reason or "The receptionist couldn't understand them.")
        self.ended = True
        self.state = "ended"
        return self._say("I'm sorry, I'm having trouble understanding you. Please call back any time, or try from a "
                         "quieter place. Goodbye!", end_call=True)

    def _unclear(self, confidence: float = 1.0) -> Reply:
        self.misunderstood += 1
        if self.misunderstood >= 3:
            return self._escalate()
        # Speech recognition wasn't sure what it heard: say so, rather than suggesting the caller made no sense.
        sorry = "Sorry, the line wasn't clear." if confidence < 0.4 else "Sorry, I didn't quite catch that."
        if self.state in ("idle", "anything_else"):
            if self.misunderstood == 1:
                return self._say(f"{sorry} I can help with appointments, our hours, location, "
                                 "or general questions. What can I do for you?")
            return self._say("I'm sorry, I'm still not sure I understood. Could you say it a different way, or would you "
                             "like a member of our team to call you back?")
        return self._say(f"{sorry} {self._reprompt()}")

    # ------------------------------------------------------------------ summary
    def detected_intent(self) -> str:
        for f in ("urgent", "booked", "rescheduled", "cancelled", "callback", "transferred"):
            if f in self.outcome_flags:
                return {"urgent": "urgent", "booked": "booking", "rescheduled": "reschedule", "cancelled": "cancellation",
                        "callback": "callback", "transferred": "transfer"}[f]
        priority = ["book", "reschedule", "cancel", "check_appointment", "human", "price", "hours", "location", "services"]
        for p in priority:
            if p in self.intents_seen:
                return {"book": "booking", "check_appointment": "appointment_question", "human": "callback",
                        "price": "pricing_question", "hours": "hours_question", "location": "location_question",
                        "services": "services_question"}.get(p, p)
        return "general_question" if self.intents_seen else "unknown"

    def summary(self) -> str:
        who = self.caller_name or "Caller"
        topics = [i for i in self.intents_seen if i in ("hours", "location", "price", "services")]
        parts = []
        if topics:
            parts.append("asked about " + ", ".join({"price": "pricing"}.get(t, t) for t in topics))
        parts.extend(a for a in self.actions if not a.startswith(("checked", "looked up")))
        if not parts:
            return f"{who} called; no specific request was captured."
        return f"{who} " + "; ".join(dict.fromkeys(parts)) + "."

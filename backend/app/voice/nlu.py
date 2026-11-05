"""Deterministic language understanding for phone conversations.

Rule-based intent detection plus entity extraction (dates, times, ordinals, phone numbers,
names, services). Caller speech is treated purely as data: nothing here executes it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
MONTH_ABBR = {m[:3]: i + 1 for i, m in enumerate(MONTHS)} | {"sept": 9}
NUM_WORDS = {
    "zero": 0, "oh": 0, "o": 0, "one": 1, "two": 2, "three": 3, "four": 4, "for": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50,
}
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5,
            "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
            "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
            "eighteenth": 18, "nineteenth": 19, "twentieth": 20, "thirtieth": 30}


def norm(text: str) -> str:
    t = text.lower().replace("’", "'")
    t = re.sub(r"[^a-z0-9:'$+ ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def has(t: str, *patterns: str) -> bool:
    return any(re.search(p, t) for p in patterns)


# ------------------------------------------------------------------ intents
END_CALL = r"\b(bye|goodbye|good bye|hang up|wrong number|end (of |the )*call)\b"

INTENT_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("injection", (r"ignore (all |your |the )?(previous|prior|above) instructions", r"system prompt", r"developer mode",
                   r"\b(password|api key|secret key|database|sql)\b", r"other (business|clinic|compan)", r"\bjailbreak")),
    ("human", (r"\b(speak|talk) (to|with) (a |an |the )?(real |actual )?(person|human|someone|somebody|receptionist|manager|doctor|dentist|staff|representative|agent)",
               r"\b(real|actual) person\b", r"\bhuman\b", r"\boperator\b", r"call me back", r"\bcallback\b",
               r"(have|get) (someone|somebody) (to )?call", r"leave a message", r"transfer me")),
    ("reschedule", (r"\breschedul", r"\bmove (my|the) (appointment|booking)", r"\bchange (my|the) (appointment|booking|time)",
                    r"\bpush (it|my appointment) back", r"different (time|day) for my")),
    ("cancel", (r"\bcancel", r"\bcan't make (it|my appointment)", r"\bcannot make (it|my appointment)", r"\bcall off")),
    ("check_appointment", (r"when is my (appointment|booking)", r"do i have an? (appointment|booking)", r"confirm my appointment",
                           r"what time is my", r"\babout (my|that|the) (appointment|booking)")),
    ("book", (r"\bbook", r"\bappointment\b", r"\bschedule\b", r"\bmake an? (appointment|reservation)", r"\breserv",
              r"\bcome in\b", r"\bsee (the |a )?(doctor|dentist|hygienist|stylist)", r"\bavailab", r"\bopenings\b",
              r"\bany opening\b", r"\bslot\b", r"\bget (a|an|my) [a-z ]{0,20}(cleaning|haircut|exam|checkup|check up|consult|viewing|massage|visit)")),
    ("hours", (r"\b(hours|open|close|closing|opening)\b", r"what time do you", r"are you (open|closed)")),
    ("location", (r"\b(address|located|location|directions|where are you|where is|find you|parking|park)\b",)),
    ("price", (r"\b(price|cost|how much|fee|charge|rates?|expensive)\b", r"\$")),
    ("services", (r"what (services|do you (offer|do|provide))", r"\bservices\b", r"do you (offer|do|provide)", r"what kind of")),
    ("repeat", (r"\b(repeat|say (that|it) again|pardon|come again|didn't (catch|hear)|sorry what)\b", r"^what$")),
    ("goodbye", (END_CALL, r"\b(that's all|that is all|that's it|nothing else|no thank you|no thanks|have a good)\b")),
    ("thanks", (r"\b(thank|thanks|appreciate)\b",)),
]

AFFIRM = re.compile(r"^(yes|yeah|yep|yup|sure|correct|right|ok|okay|alright|all right|perfect|great|absolutely|definitely|"
                    r"please|please do|go ahead|go for it|sounds good|sounds great|that works|that's right|that is right|"
                    r"that's correct|that's fine|that is fine|that's good|that would be great|that'd be great|exactly|fine|"
                    r"uh huh|mhm|mm hmm|mmhmm|i think so|no problem|no worries|no that works|of course|book it|do it|confirm)\b")
DENY = re.compile(r"^(no(?! problem\b| worries\b| that works\b)|nope|nah|not really|incorrect|wrong|that's wrong|that is wrong|"
                  r"neither|none|not quite|actually no|wait|hold on|cancel that|that's not right|negative)\b")
GREETING = re.compile(r"^(hello|hi|hey|good (morning|afternoon|evening))( there)?$")
HEARING_CHECK = re.compile(r"^((hello|hi|hey) )?(are you there|can you hear me|do you hear me)$")

HEALTH_EMERGENCY = (
    r"\bemergency\b", r"\b(severe|extreme|unbearable|excruciating|terrible) pain", r"bleeding (a lot|heavily|won't stop|that won't stop|non stop)",
    r"can't stop (the )?bleeding", r"\b(won't|wont|cannot|not) stop(ped)? bleeding", r"\bswelling\b.*\b(face|jaw|eye|throat|neck)\b",
    r"\b(face|jaw|eye|throat|neck)\b.*\bswollen\b", r"\bswollen\b.*\b(face|jaw|eye|throat|neck)\b",
    r"\b(can't|cannot|hard to|trouble|difficulty) (breathe|breathing|swallow)",
    r"\bchest pain", r"\bunconscious\b", r"\bpassed out\b", r"\bknocked out\b", r"\bbroken jaw\b", r"\boverdose",
    r"\bsuicid", r"\bkill myself\b", r"\bheart attack\b", r"\bstroke\b", r"\bseizure\b", r"\ballergic reaction\b",
    r"\bhigh fever\b", r"\bfacial swelling\b",
)
GENERAL_EMERGENCY = (r"\bemergency\b", r"\bfire\b", r"\bflood(ing)?\b", r"\bgas leak\b", r"\bsmell gas\b", r"\bburst pipe\b",
                     r"\bno heat\b", r"\bbreak in\b", r"\bbroken into\b", r"\bsmoke\b", r"\bcarbon monoxide\b")
MEDICAL_ADVICE = (r"should i take", r"what (medication|medicine|pills?|antibiotics?)", r"\bdiagnos", r"is it normal",
                  r"is (this|that|it) serious", r"what('s| is) wrong with", r"\bprescri", r"how (long|much) should i take")


def detect_intents(text: str) -> list[str]:
    t = norm(text)
    found = [name for name, pats in INTENT_PATTERNS if has(t, *pats)]
    # "book" + "cancel/reschedule" -> the specific action wins
    if "reschedule" in found or "cancel" in found:
        found = [f for f in found if f != "book"]
    if "check_appointment" in found:
        found = [f for f in found if f not in ("book", "hours")]
    if "book" in found and "hours" in found and not has(t, r"\bhours\b", r"what time do you", r"are you open"):
        found.remove("hours")
    return found


def is_emergency(text: str, health_business: bool) -> bool:
    # "Do you do emergency appointments?" asks about a service. Anything else in the sentence still counts.
    t = re.sub(r"\bdo you (do|see|take|have|offer|handle|accept|treat|provide) .*emergenc\w*", "", norm(text))
    return has(t, *(HEALTH_EMERGENCY if health_business else GENERAL_EMERGENCY))


def wants_medical_advice(text: str) -> bool:
    return has(norm(text), *MEDICAL_ADVICE)


def is_affirm(text: str) -> bool:
    t = norm(text)
    return bool(AFFIRM.match(t)) and not DENY.match(t)


def is_deny(text: str) -> bool:
    return bool(DENY.match(norm(text)))


def is_greeting(text: str) -> bool:
    return bool(GREETING.match(norm(text)))


def is_hearing_check(text: str) -> bool:
    return bool(HEARING_CHECK.match(norm(text)))


# ------------------------------------------------------------------ dates & times
@dataclass
class When:
    dates: list[date] = field(default_factory=list)  # candidate local dates
    time: time | None = None
    part_of_day: str | None = None  # morning | afternoon | evening
    flexible: bool = False  # e.g. "any time", "earliest"
    explicit_date: bool = False

    @property
    def empty(self) -> bool:
        return not self.dates and self.time is None and self.part_of_day is None and not self.flexible


def _num(tok: str) -> int | None:
    if tok.isdigit():
        return int(tok)
    return NUM_WORDS.get(tok)


def parse_time(t: str) -> time | None:
    t = norm(t)
    if has(t, r"\bnoon\b", r"\bmidday\b"):
        return time(12, 0)
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(a\s?m|p\s?m)?\b", t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").replace(" ", "")
        return _mk_time(h, mi, ap)
    m = re.search(r"\b(\d{1,2})\s*(a\s?m|p\s?m)\b", t)
    if m:
        return _mk_time(int(m.group(1)), 0, m.group(2).replace(" ", ""))
    m = re.search(r"\bhalf past (\w+)", t)
    if m and (h := _num(m.group(1))) is not None:
        return _mk_time(h, 30, _ampm_hint(t))
    m = re.search(r"\bquarter past (\w+)", t)
    if m and (h := _num(m.group(1))) is not None:
        return _mk_time(h, 15, _ampm_hint(t))
    m = re.search(r"\b(?:at|around|about|by)\s+(\w+)(?:\s+(o'clock|thirty|fifteen|forty five|forty-five|\d{2}))?(?:\s+(a\s?m|p\s?m|in the morning|in the afternoon|in the evening))?", t)
    if m:
        h = _num(m.group(1))
        if h is not None and 1 <= h <= 12:
            mins = {"thirty": 30, "fifteen": 15, "forty five": 45, "forty-five": 45}.get(m.group(2) or "", 0)
            if m.group(2) and m.group(2).isdigit():
                mins = int(m.group(2))
            return _mk_time(h, mins, (m.group(3) or _ampm_hint(t)).replace(" ", ""))
    m = re.search(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(thirty|fifteen|forty five)\b", t)
    if m:
        return _mk_time(NUM_WORDS[m.group(1)], {"thirty": 30, "fifteen": 15, "forty five": 45}[m.group(2)], _ampm_hint(t))
    m = re.search(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+o'?clock\b", t)
    if m:
        return _mk_time(NUM_WORDS[m.group(1)], 0, _ampm_hint(t))
    return None


def _ampm_hint(t: str) -> str:
    if has(t, r"\bmorning\b", r"\ba\s?m\b"):
        return "am"
    if has(t, r"\b(afternoon|evening)\b", r"\bp\s?m\b"):
        return "pm"
    return ""


def _mk_time(h: int, m: int, ap: str) -> time | None:
    ap = ap.replace(".", "")
    if ap.startswith("inthemorning"):
        ap = "am"
    elif ap.startswith("inthe"):
        ap = "pm"
    if not (0 <= m < 60) or not (0 <= h <= 23):
        return None
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 6:
        h += 12  # "at 3" in a business context means 3 PM
    return time(h, m)


def parse_when(text: str, today: date) -> When:
    t = norm(text)
    w = When()
    if has(t, r"\b(any ?time|whenever|earliest|first available|soonest|as soon as possible|asap|next available|don't mind|doesn't matter)\b"):
        w.flexible = True
    if has(t, r"\bmorning\b"):
        w.part_of_day = "morning"
    elif has(t, r"\bafternoon\b"):
        w.part_of_day = "afternoon"
    elif has(t, r"\bevening\b", r"after work"):
        w.part_of_day = "evening"

    if has(t, r"\btoday\b", r"\bthis (morning|afternoon|evening)\b"):
        w.dates, w.explicit_date = [today], True
    elif has(t, r"\b(day after tomorrow)\b"):
        w.dates, w.explicit_date = [today + timedelta(days=2)], True
    elif has(t, r"\btomorrow\b"):
        w.dates, w.explicit_date = [today + timedelta(days=1)], True
    else:
        wd_match = re.search(r"\b(next|this|coming)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", t)
        month_match = re.search(r"\b(" + "|".join(MONTHS + list(MONTH_ABBR)) + r")\.?\s+(\d{1,2}|\w+)(?:st|nd|rd|th)?\b", t)
        day_first = re.search(r"\b(?:the\s+)?(\d{1,2}(?:st|nd|rd|th)|" + "|".join(ORDINALS) + r")(?:\s+of\s+(" + "|".join(MONTHS + list(MONTH_ABBR)) + r"))?\b", t)
        month_day = None
        if month_match:  # "May I come in" also looks like a month until there's a day after it
            mname = month_match.group(1)
            month = MONTHS.index(mname) + 1 if mname in MONTHS else MONTH_ABBR.get(mname[:3])
            dtok = month_match.group(2)
            day = int(dtok) if dtok.isdigit() else ORDINALS.get(dtok) or NUM_WORDS.get(dtok)
            if month and day:
                month_day = (month, day)
        if month_day:
            w.dates, w.explicit_date = _future_date(today, *month_day), True
        elif wd_match:
            target = WEEKDAYS.index(wd_match.group(2))
            if wd_match.group(1) == "next" or has(t, r"\bnext week\b"):
                delta = 7 - today.weekday() + target  # that weekday in the next calendar week
            else:
                delta = (target - today.weekday()) % 7
            w.dates, w.explicit_date = [today + timedelta(days=delta)], True
        elif has(t, r"\bnext week\b"):
            start = today + timedelta(days=7 - today.weekday())
            w.dates = [start + timedelta(days=i) for i in range(7)]
        elif has(t, r"\bthis week\b", r"\blater this week\b"):
            w.dates = [today + timedelta(days=i) for i in range(0, 7 - today.weekday())]
        elif has(t, r"\b(this )?weekend\b"):
            sat = today + timedelta(days=(5 - today.weekday()) % 7)
            w.dates = [sat, sat + timedelta(days=1)]
        elif has(t, r"\bnext month\b"):
            first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
            w.dates = [first + timedelta(days=i) for i in range(28)]
        elif day_first and (day_first.group(2) or has(t, r"\bthe (\d{1,2}(st|nd|rd|th)|" + "|".join(ORDINALS) + r")\b")):
            tok = day_first.group(1)
            day = int(re.sub(r"\D", "", tok)) if tok[0].isdigit() else ORDINALS[tok]
            if day_first.group(2):
                mname = day_first.group(2)
                month = MONTHS.index(mname) + 1 if mname in MONTHS else MONTH_ABBR[mname[:3]]
                w.dates = _future_date(today, month, day)
            else:
                w.dates = _next_day_of_month(today, day)
            w.explicit_date = bool(w.dates)
    w.time = parse_time(text)
    return w


def _future_date(today: date, month: int, day: int) -> list[date]:
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return []
        if d >= today:
            return [d]
    return []


def _next_day_of_month(today: date, day: int) -> list[date]:
    y, m = today.year, today.month
    for _ in range(3):
        try:
            d = date(y, m, day)
            if d >= today:
                return [d]
        except ValueError:
            pass
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return []


# ------------------------------------------------------------------ choices, phone numbers, names
def parse_choice(text: str, n_options: int) -> int | None:
    """Return 0-based index for 'the first one', 'second', 'the last one', 'option 2'."""
    t = norm(text)
    if has(t, r"\blast( one)?\b") and n_options:
        return n_options - 1
    for word, idx in (("first", 0), ("1st", 0), ("second", 1), ("2nd", 1), ("third", 2), ("3rd", 2)):
        if re.search(rf"\b{word}\b", t) and idx < n_options:
            return idx
    m = re.search(r"\b(?:option|number)\s+(one|two|three|1|2|3)\b", t)
    if m:
        idx = (_num(m.group(1)) or 0) - 1
        return idx if 0 <= idx < n_options else None
    return None


DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "to": "2", "too": "2", "three": "3",
               "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "ate": "8", "nine": "9"}


def phone_digits(text: str) -> str:
    """Every digit in what the caller said, in order, including spoken ones like "double three"."""
    t = norm(text).replace("-", " ")
    tokens = t.split()
    digits = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("double", "triple") and i + 1 < len(tokens):
            nxt = DIGIT_WORDS.get(tokens[i + 1]) or (tokens[i + 1] if tokens[i + 1].isdigit() and len(tokens[i + 1]) == 1 else "")
            if nxt:
                digits += nxt * (2 if tok == "double" else 3)
                i += 2
                continue
        if tok.startswith("+") and tok[1:].isdigit():
            digits += tok[1:]
        elif tok.isdigit():
            digits += tok
        elif tok in DIGIT_WORDS and (digits or tok not in ("to", "too", "for", "o", "ate")):
            digits += DIGIT_WORDS[tok]
        i += 1
    return digits


def phone_from_digits(digits: str) -> str | None:
    """A complete phone number, or None if there aren't enough digits yet. A number starting with 0 is in some
    country's local format: it stays as spoken, because adding a country code would be a guess."""
    if digits.startswith("0"):
        return digits if 10 <= len(digits) <= 15 else None
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if 11 <= len(digits) <= 15:
        return "+" + digits
    return None


def extract_phone(text: str) -> str | None:
    return phone_from_digits(phone_digits(text))


NAME_PREFIX = re.compile(r"\b(?:my name is|my name's|name is|this is|it's|it is|i am|i'm|call me|the name is|under)\s+(.+)", re.I)
NOT_NAMES = {"yes", "no", "okay", "ok", "sure", "hello", "hi", "hey", "um", "uh", "the", "a", "and", "calling", "looking",
             "trying", "wondering", "interested", "here", "fine", "good", "great", "sorry", "just", "first", "second", "third",
             "last", "one", "two", "three", "option", "that", "this", "next", "tuesday", "monday", "wednesday", "thursday",
             "friday", "saturday", "sunday", "morning", "afternoon", "evening", "what", "can", "could", "would", "i",
             "not", "nothing", "never", "perfect", "correct", "right", "wrong", "nope", "yeah", "yep"}


def extract_name(text: str, expecting: bool = False) -> str | None:
    raw = text.strip().strip(".!?, ")
    m = NAME_PREFIX.search(raw)
    cand = m.group(1) if m else (raw if expecting else None)
    if not cand:
        return None
    cand = re.split(r"[,.!?]| and | my | i | phone | number ", cand, maxsplit=1)[0]
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", cand)][:4]
    while words and words[0].lower() in NOT_NAMES | {"mr", "mrs", "ms", "miss", "dr"}:
        words.pop(0)
    if not words or words[0].lower() in NOT_NAMES or len(words) > 4:
        return None
    if any(w.lower() in ("appointment", "book", "cleaning", "tomorrow", "today", "please") for w in words):
        return None
    return " ".join(w[:1].upper() + w[1:].lower() if w.islower() or w.isupper() else w for w in words)


# ------------------------------------------------------------------ fuzzy matching (services, KB)
STOP = set("a an the i to for of and or is are do does you your my me we our can could would like want need please "
           "get have has be on in at with about what how when where which who this that it its im i'm just some any "
           "there here hi hello yes no so um uh see know tell think mean say said go going got let make take "
           "ok okay yeah".split())


def tokens(text: str) -> set[str]:
    out = set()
    for w in norm(text).split():
        if w in STOP or len(w) < 2:
            continue
        for suf in ("ings", "ing", "es", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                w = w[: -len(suf)]
                break
        out.add(w)
    return out


SYNONYMS = {"clean": {"cleaning", "hygiene", "scale", "polish"}, "checkup": {"exam", "check"}, "exam": {"checkup", "check"},
            "cut": {"haircut", "trim"}, "haircut": {"cut", "trim"}, "consult": {"consultation"}, "consultation": {"consult"},
            "color": {"colour", "dye"}, "view": {"viewing", "tour", "showing"}, "tooth": {"teeth"}, "teeth": {"tooth"}}


def match_service(text: str, services: list[tuple[str, str]]) -> str | None:
    """services: list of (id, name). Returns best-matching id or None."""
    tt = tokens(text)
    expanded = set(tt)
    for w in tt:
        expanded |= SYNONYMS.get(w, set())
    best, best_score = None, 0.0
    for sid, name in services:
        nt = tokens(name)
        if not nt:
            continue
        if norm(name) in norm(text):
            return sid
        overlap = len(nt & expanded)
        score = overlap / len(nt)
        if overlap and score > best_score:
            best, best_score = sid, score
    return best if best_score >= 0.5 else None


def kb_score(query: str, question: str, answer: str) -> float:
    q = tokens(query)
    if not q:
        return 0.0
    qq, aa = tokens(question), tokens(answer)
    s = len(q & qq) / max(1, len(q)) * 1.0 + len(q & aa) / max(1, len(q)) * 0.35
    return s

"""Time-zone helpers. Storage is always UTC; business rules are evaluated in the tenant's zone."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def local_to_utc(d: date, t: time, tz: ZoneInfo) -> datetime | None:
    """Convert a local wall-clock time to UTC. Returns None for times skipped by a DST jump."""
    local = datetime.combine(d, t).replace(tzinfo=tz)
    utc = local.astimezone(timezone.utc)
    roundtrip = utc.astimezone(tz)
    if roundtrip.replace(tzinfo=None) != local.replace(tzinfo=None):
        return None  # non-existent local time (spring forward gap)
    return utc


def to_local(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.astimezone(tz)


def local_today(tz: ZoneInfo, now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(tz).date()


def spoken_date(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    day = d.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{d.strftime('%A')}, {d.strftime('%B')} {day}{suffix}"


def spoken_time(t: time | datetime) -> str:
    hour = t.hour % 12 or 12
    ampm = "AM" if t.hour < 12 else "PM"
    if t.minute == 0:
        return f"{hour} {ampm}"
    return f"{hour}:{t.minute:02d} {ampm}"


ALL_TIMEZONES = sorted(z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/")))

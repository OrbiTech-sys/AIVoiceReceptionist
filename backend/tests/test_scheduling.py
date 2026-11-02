from __future__ import annotations

import threading
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.errors import AppError
from app.models import (
    Appointment,
    HolidayException,
    Notification,
    ReminderJob,
    Service,
    StaffAvailability,
    StaffMember,
    StaffService,
    Tenant,
    User,
)
from app.security import hash_password
from app.services import reminders, scheduling
from app.services.tenants import create_tenant
from app.services.timeutil import local_to_utc

NY = ZoneInfo("America/New_York")
# A fixed "now": Monday 2026-10-26 08:00 New York time.
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=NY).astimezone(timezone.utc)


def make_tenant(tz="America/New_York", name="Clinic") -> tuple[str, str]:
    with SessionLocal() as db:
        u = User(email=f"{name.lower()}@example.com", password_hash=hash_password("Sup3rSecret!"), full_name="O")
        db.add(u)
        db.flush()
        t = create_tenant(db, name=name, business_type="dental", timezone=tz, owner_user_id=u.id)
        db.commit()
        svc = db.scalar(select(Service).where(Service.tenant_id == t.id, Service.name == "Dental cleaning"))
        return t.id, svc.id


def at(d: date, h: int, m: int = 0, tz=NY) -> datetime:
    return datetime.combine(d, time(h, m), tzinfo=tz).astimezone(timezone.utc)


def book(tid, sid, start, name="Pat", phone="+14155550100", **kw):
    with SessionLocal() as db:
        t = db.get(Tenant, tid)
        return scheduling.book_appointment(db, t, service_id=sid, start=start, customer_name=name,
                                           customer_phone=phone, now=NOW, **kw)


def slots(tid, sid, d: date, **kw):
    with SessionLocal() as db:
        t, s = db.get(Tenant, tid), db.get(Service, sid)
        return scheduling.find_slots(db, t, s, d, d, now=NOW, **kw)


def test_slots_respect_hours_break_and_duration():
    tid, sid = make_tenant()  # Mon-Fri 9-17, lunch 12-13, 60-min cleaning, 30-min interval
    tue = date(2026, 10, 27)
    starts = [s.start.astimezone(NY).time() for s in slots(tid, sid, tue)]
    assert starts[0] == time(9, 0)
    assert time(11, 0) in starts and time(11, 30) not in starts  # would overlap lunch
    assert time(12, 0) not in starts and time(13, 0) in starts
    assert starts[-1] == time(16, 0)  # last 60-minute slot ends at close
    assert slots(tid, sid, date(2026, 10, 31)) == []  # Saturday closed


def test_min_notice_and_holiday():
    tid, sid = make_tenant()
    today = date(2026, 10, 26)  # now is 08:00, min notice 120 min -> first slot 10:00
    first = slots(tid, sid, today)[0].start.astimezone(NY).time()
    assert first == time(10, 0)
    with SessionLocal() as db:
        db.add(HolidayException(tenant_id=tid, date=date(2026, 10, 28), label="Closed", is_closed=True))
        db.commit()
    assert slots(tid, sid, date(2026, 10, 28)) == []


def test_booking_prevents_overlap_and_applies_buffer():
    tid, sid = make_tenant()
    tue = date(2026, 10, 27)
    with SessionLocal() as db:
        db.get(Service, sid).buffer_min = 15
        db.commit()
    a = book(tid, sid, at(tue, 9))
    assert a.status == "confirmed" and a.source == "dashboard"
    with pytest.raises(scheduling.SlotUnavailable):
        book(tid, sid, at(tue, 9), name="Other", phone="+14155550199")
    # 10:00 is blocked by the 15-minute buffer after 9:00-10:00; 10:30 is free.
    starts = [s.start.astimezone(NY).time() for s in slots(tid, sid, tue)]
    assert time(10, 0) not in starts and time(10, 30) in starts
    with pytest.raises(scheduling.SlotUnavailable):
        book(tid, sid, at(tue, 10), name="Other", phone="+14155550199")


def test_outside_hours_rejected_unless_manual_override():
    tid, sid = make_tenant()
    tue = date(2026, 10, 27)
    with pytest.raises(scheduling.SlotUnavailable):
        book(tid, sid, at(tue, 19))
    a = book(tid, sid, at(tue, 19), enforce_rules=False)
    assert a.start_at == at(tue, 19)
    with pytest.raises(scheduling.SlotUnavailable):  # overrides never allow overlaps
        book(tid, sid, at(tue, 19, 30), name="X", phone="+14155550111", enforce_rules=False)


def test_concurrent_bookings_only_one_wins():
    tid, sid = make_tenant()
    start = at(date(2026, 10, 27), 14)
    results: list[str] = []
    barrier = threading.Barrier(6)

    def worker(i):
        barrier.wait()
        try:
            book(tid, sid, start, name=f"Caller {i}", phone=f"+1415555020{i}")
            results.append("ok")
        except scheduling.SlotUnavailable:
            results.append("taken")
        except Exception as exc:  # pragma: no cover - surfaced in assertion
            results.append(type(exc).__name__)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1, results
    assert results.count("taken") == 5, results
    with SessionLocal() as db:
        assert db.query(Appointment).count() == 1


def test_staff_calendars_and_availability():
    tid, sid = make_tenant()
    with SessionLocal() as db:
        a = StaffMember(tenant_id=tid, name="Ann")
        b = StaffMember(tenant_id=tid, name="Ben")
        db.add_all([a, b])
        db.flush()
        for s in (a, b):
            db.add(StaffService(staff_id=s.id, service_id=sid, tenant_id=tid))
        db.add(StaffAvailability(tenant_id=tid, staff_id=b.id, weekday=1, start_time=time(13), end_time=time(17)))
        db.commit()
        ann, ben = a.id, b.id
    tue = date(2026, 10, 27)
    at9 = [s for s in slots(tid, sid, tue) if s.start == at(tue, 9)]
    assert [s.staff_id for s in at9] == [ann]  # Ben only works afternoons on Tuesday
    # Two different staff can be booked at the same time.
    book(tid, sid, at(tue, 14), name="P1", phone="+14155550301")
    book(tid, sid, at(tue, 14), name="P2", phone="+14155550302")
    with SessionLocal() as db:
        staff_ids = {x.staff_id for x in db.query(Appointment)}
    assert staff_ids == {ann, ben}
    with pytest.raises(scheduling.SlotUnavailable):
        book(tid, sid, at(tue, 14), name="P3", phone="+14155550303")


def test_reschedule_cancel_and_transitions():
    tid, sid = make_tenant()
    tue = date(2026, 10, 27)
    a = book(tid, sid, at(tue, 9))
    with SessionLocal() as db:
        t = db.get(Tenant, tid)
        moved = scheduling.reschedule_appointment(db, t, a.id, at(tue, 15), now=NOW)
        assert moved.start_at == at(tue, 15)
    # The old slot is free again.
    assert at(tue, 9) in [s.start for s in slots(tid, sid, tue)]
    with SessionLocal() as db:
        t = db.get(Tenant, tid)
        with pytest.raises(AppError):  # can't complete a future appointment
            scheduling.change_status(db, t, a.id, "completed", now=NOW)
        c = scheduling.change_status(db, t, a.id, "cancelled", reason="sick", now=NOW)
        assert c.status == "cancelled"
        with pytest.raises(AppError):  # cancelled is terminal
            scheduling.change_status(db, t, a.id, "confirmed", now=NOW)
        assert db.scalar(select(ReminderJob.status).where(ReminderJob.appointment_id == a.id)) == "cancelled"
        kinds = [n.kind for n in db.scalars(select(Notification).where(Notification.tenant_id == tid))]
    assert {"appointment_booked", "appointment_rescheduled", "appointment_cancelled"} <= set(kinds)
    assert at(tue, 15) in [s.start for s in slots(tid, sid, tue)]


def test_caller_cancellation_respects_notice_policy():
    tid, sid = make_tenant()
    a = book(tid, sid, at(date(2026, 10, 26), 14))  # today 14:00, policy requires 24h notice
    with SessionLocal() as db:
        t = db.get(Tenant, tid)
        with pytest.raises(AppError) as e:
            scheduling.change_status(db, t, a.id, "cancelled", by_caller=True, now=NOW)
        assert e.value.code == "policy_notice"


def test_contacts_deduplicated_by_normalized_phone():
    tid, sid = make_tenant()
    tue = date(2026, 10, 27)
    a1 = book(tid, sid, at(tue, 9), name="Sam", phone="(415) 555-0100")
    a2 = book(tid, sid, at(tue, 13), name="Sam", phone="+1 415 555 0100")
    assert a1.contact_id == a2.contact_id


def test_dst_transition_slots_are_correct():
    # US DST ends 2026-11-01 (Sunday). Monday after: 9 AM EST == 14:00 UTC (was 13:00 UTC the week before).
    tid, sid = make_tenant()
    mon_before, mon_after = date(2026, 10, 26), date(2026, 11, 2)
    assert at(mon_before, 9).hour == 13 and at(mon_after, 9).hour == 14
    first_after = slots(tid, sid, mon_after)[0].start
    assert first_after == datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)
    # Spring-forward gap: 02:30 local doesn't exist on 2026-03-08.
    assert local_to_utc(date(2026, 3, 8), time(2, 30), NY) is None


def test_reminders_scheduled_once_and_sent_once():
    tid, sid = make_tenant()
    tue = date(2026, 10, 27)
    a = book(tid, sid, at(tue, 9))
    with SessionLocal() as db:
        jobs = db.scalars(select(ReminderJob).where(ReminderJob.appointment_id == a.id)).all()
        assert len(jobs) == 1 and jobs[0].offset_minutes == 60
        assert jobs[0].due_at == at(tue, 8)  # 1 hour before, in absolute time
        # Re-scheduling the same appointment must not create duplicates.
        appt = db.get(Appointment, a.id)
        reminders.schedule_for_appointment(db, appt, now=NOW)
        reminders.schedule_for_appointment(db, appt, now=NOW)
        db.commit()
        assert db.query(ReminderJob).filter(ReminderJob.appointment_id == a.id).count() == 1

    due_time = at(tue, 8, 1)
    results = []

    def run():
        with SessionLocal() as db:
            results.append(reminders.run_due(db, now=due_time))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == 1, results
    with SessionLocal() as db:
        assert db.query(Notification).filter(Notification.kind == "appointment_reminder").count() == 1
        assert db.scalar(select(ReminderJob.status).where(ReminderJob.appointment_id == a.id)) == "sent"
        # Running again later sends nothing new.
        assert reminders.run_due(db, now=due_time + timedelta(minutes=5)) == 0


def test_reminder_retimed_on_reschedule():
    tid, sid = make_tenant()
    tue = date(2026, 10, 27)
    a = book(tid, sid, at(tue, 9))
    with SessionLocal() as db:
        scheduling.reschedule_appointment(db, db.get(Tenant, tid), a.id, at(tue, 15), now=NOW)
        job = db.scalar(select(ReminderJob).where(ReminderJob.appointment_id == a.id))
        assert job.due_at == at(tue, 14) and job.status == "scheduled"


def test_local_to_utc_round_trip_london():
    tid, sid = make_tenant(tz="Europe/London", name="LondonCo")
    d = date(2026, 10, 27)
    first = slots(tid, sid, d)[0].start
    assert first == local_to_utc(d, time(9), ZoneInfo("Europe/London"))

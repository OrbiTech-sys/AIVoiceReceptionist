"""Relational data model. Every business-owned table carries tenant_id."""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, new_id, utcnow

ID = String(32)


def pk() -> Mapped[str]:
    return mapped_column(ID, primary_key=True, default=new_id)


def tenant_fk() -> Mapped[str]:
    return mapped_column(ID, ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------- identity & tenancy
class User(Timestamped, Base):
    __tablename__ = "users"
    id: Mapped[str] = pk()
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    memberships: Mapped[list[Membership]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Tenant(Timestamped, Base):
    __tablename__ = "tenants"
    id: Mapped[str] = pk()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    business_type: Mapped[str] = mapped_column(String(40), nullable=False, default="other")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    address: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    directions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    website: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    email: Mapped[str] = mapped_column(String(254), default="", nullable=False)
    onboarding_step: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    after_hours_behavior: Mapped[str] = mapped_column(String(20), default="take_message", nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # privacy / compliance configuration
    recording_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recording_consent_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ai_disclosure_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    transcript_retention_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    recording_retention_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    emergency_policy: Mapped[str] = mapped_column(Text, default="", nullable=False)
    __table_args__ = (
        CheckConstraint("after_hours_behavior in ('take_message','book_only','closed_message')", name="ck_tenant_after_hours"),
    )


class Membership(Timestamped, Base):
    __tablename__ = "memberships"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[str] = tenant_fk()
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="staff")
    user: Mapped[User] = relationship(back_populates="memberships")
    tenant: Mapped[Tenant] = relationship()
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),
        CheckConstraint("role in ('owner','staff')", name="ck_membership_role"),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    active_tenant_id: Mapped[str | None] = mapped_column(ID, ForeignKey("tenants.id", ondelete="SET NULL"))
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    user_agent: Mapped[str] = mapped_column(String(200), default="", nullable=False)


# ---------------------------------------------------------------- business configuration
class BusinessHours(Base):
    __tablename__ = "business_hours"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Monday
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    open_time: Mapped[time | None] = mapped_column(Time)
    close_time: Mapped[time | None] = mapped_column(Time)
    break_start: Mapped[time | None] = mapped_column(Time)
    break_end: Mapped[time | None] = mapped_column(Time)
    __table_args__ = (
        UniqueConstraint("tenant_id", "weekday", name="uq_hours_tenant_weekday"),
        CheckConstraint("weekday >= 0 and weekday <= 6", name="ck_hours_weekday"),
    )


class HolidayException(Base):
    __tablename__ = "holiday_exceptions"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    date: Mapped[date] = mapped_column(Date, nullable=False)
    label: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    open_time: Mapped[time | None] = mapped_column(Time)
    close_time: Mapped[time | None] = mapped_column(Time)
    __table_args__ = (UniqueConstraint("tenant_id", "date", name="uq_holiday_tenant_date"),)


class ReceptionistSettings(Timestamped, Base):
    __tablename__ = "receptionist_settings"
    tenant_id: Mapped[str] = mapped_column(ID, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    name: Mapped[str] = mapped_column(String(60), default="Ava", nullable=False)
    voice_id: Mapped[str] = mapped_column(String(40), default="af_heart", nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="en-US", nullable=False)
    style: Mapped[str] = mapped_column(String(20), default="warm", nullable=False)
    speed: Mapped[float] = mapped_column(default=1.0, nullable=False)
    greeting: Mapped[str] = mapped_column(Text, default="", nullable=False)
    closing: Mapped[str] = mapped_column(Text, default="", nullable=False)
    transfer_number: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    escalation_contact: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    boundaries: Mapped[str] = mapped_column(Text, default="", nullable=False)
    __table_args__ = (CheckConstraint("style in ('formal','warm','friendly','professional')", name="ck_recep_style"),)


class BookingPolicy(Timestamped, Base):
    __tablename__ = "booking_policies"
    tenant_id: Mapped[str] = mapped_column(ID, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    slot_interval_min: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    min_notice_min: Mapped[int] = mapped_column(Integer, default=120, nullable=False)
    max_advance_days: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    default_buffer_min: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cancellation_notice_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    reschedule_notice_hours: Mapped[int] = mapped_column(Integer, default=12, nullable=False)
    cancellation_policy_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    auto_confirm: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Service(Timestamped, Base):
    __tablename__ = "services"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    buffer_min: Mapped[int | None] = mapped_column(Integer)
    price_text: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_service_tenant_name"),
        CheckConstraint("duration_min > 0 and duration_min <= 600", name="ck_service_duration"),
    )


class StaffMember(Timestamped, Base):
    __tablename__ = "staff_members"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    email: Mapped[str] = mapped_column(String(254), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    availability: Mapped[list[StaffAvailability]] = relationship(cascade="all, delete-orphan", order_by="StaffAvailability.weekday")
    service_links: Mapped[list[StaffService]] = relationship(cascade="all, delete-orphan")


class StaffService(Base):
    __tablename__ = "staff_services"
    staff_id: Mapped[str] = mapped_column(ID, ForeignKey("staff_members.id", ondelete="CASCADE"), primary_key=True)
    service_id: Mapped[str] = mapped_column(ID, ForeignKey("services.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[str] = tenant_fk()


class StaffAvailability(Base):
    __tablename__ = "staff_availability"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    staff_id: Mapped[str] = mapped_column(ID, ForeignKey("staff_members.id", ondelete="CASCADE"), nullable=False, index=True)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    __table_args__ = (CheckConstraint("weekday >= 0 and weekday <= 6", name="ck_staff_avail_weekday"),)


class KnowledgeEntry(Timestamped, Base):
    __tablename__ = "knowledge_entries"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    kind: Mapped[str] = mapped_column(String(20), default="faq", nullable=False)
    question: Mapped[str] = mapped_column(String(300), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (CheckConstraint("kind in ('faq','policy','directions','general')", name="ck_kb_kind"),)


# ---------------------------------------------------------------- customers, appointments, calls
class Contact(Timestamped, Base):
    __tablename__ = "contacts"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    phone_e164: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str] = mapped_column(String(254), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    follow_up_status: Mapped[str] = mapped_column(String(12), default="none", nullable=False)
    last_interaction_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone_e164", name="uq_contact_tenant_phone"),
        CheckConstraint("follow_up_status in ('none','needed','done')", name="ck_contact_followup"),
    )


APPOINTMENT_STATUSES = ("pending", "confirmed", "completed", "cancelled", "no_show")
ACTIVE_APPOINTMENT_STATUSES = ("pending", "confirmed")


class Appointment(Timestamped, Base):
    __tablename__ = "appointments"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    contact_id: Mapped[str] = mapped_column(ID, ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False, index=True)
    service_id: Mapped[str] = mapped_column(ID, ForeignKey("services.id", ondelete="RESTRICT"), nullable=False)
    staff_id: Mapped[str | None] = mapped_column(ID, ForeignKey("staff_members.id", ondelete="SET NULL"))
    # Booking resource: staff id, or "tenant" when the business books without staff calendars.
    resource_key: Mapped[str] = mapped_column(String(40), nullable=False)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    blocked_until: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)  # end + buffer
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="confirmed")
    source: Mapped[str] = mapped_column(String(12), nullable=False, default="dashboard")
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cancel_reason: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    call_id: Mapped[str | None] = mapped_column(ID, ForeignKey("calls.id", ondelete="SET NULL"))
    created_by_user_id: Mapped[str | None] = mapped_column(ID, ForeignKey("users.id", ondelete="SET NULL"))
    contact: Mapped[Contact] = relationship()
    service: Mapped[Service] = relationship()
    staff: Mapped[StaffMember | None] = relationship()
    __table_args__ = (
        Index("ix_appt_tenant_start", "tenant_id", "start_at"),
        Index("ix_appt_resource", "tenant_id", "resource_key", "start_at"),
        CheckConstraint("end_at > start_at", name="ck_appt_range"),
        CheckConstraint("blocked_until >= end_at", name="ck_appt_blocked"),
        CheckConstraint("status in ('pending','confirmed','completed','cancelled','no_show')", name="ck_appt_status"),
        CheckConstraint("source in ('ai_call','dashboard','manual')", name="ck_appt_source"),
    )


class Call(Timestamped, Base):
    __tablename__ = "calls"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="browser_demo")
    external_id: Mapped[str | None] = mapped_column(String(80))
    caller_number: Mapped[str | None] = mapped_column(String(20))
    caller_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    contact_id: Mapped[str | None] = mapped_column(ID, ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="in_progress")
    outcome: Mapped[str | None] = mapped_column(String(16))
    intent: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    follow_up_status: Mapped[str] = mapped_column(String(12), default="none", nullable=False)
    error_detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Not a FK to avoid a circular constraint with appointments.call_id; resolved tenant-scoped in queries.
    appointment_id: Mapped[str | None] = mapped_column(ID)
    recording_path: Mapped[str | None] = mapped_column(String(300))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (
        Index("ix_calls_tenant_started", "tenant_id", "started_at"),
        CheckConstraint("channel in ('browser_demo','phone')", name="ck_call_channel"),
        CheckConstraint("status in ('in_progress','completed','failed')", name="ck_call_status"),
        CheckConstraint("follow_up_status in ('none','needed','done')", name="ck_call_followup"),
    )


class CallEvent(Base):
    __tablename__ = "call_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(ID, ForeignKey("calls.id", ondelete="CASCADE"), index=True, nullable=False)
    tenant_id: Mapped[str] = tenant_fk()
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # caller | agent | tool | state | error | latency
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON)


# ---------------------------------------------------------------- notifications
NOTIFICATION_KINDS = (
    "appointment_booked",
    "appointment_rescheduled",
    "appointment_cancelled",
    "appointment_reminder",
    "call_missed",
    "callback_requested",
    "urgent_request",
    "phone_offline",
    "system_failure",
)
URGENT_KINDS = ("urgent_request", "phone_offline", "system_failure", "call_missed")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="info")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    link: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    entity_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    event_time: Mapped[datetime | None] = mapped_column(UTCDateTime)  # e.g. appointment start
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # push outbox: pending -> sent | failed | skipped
    push_status: Mapped[str] = mapped_column(String(10), default="skipped", nullable=False)
    email_status: Mapped[str] = mapped_column(String(10), default="skipped", nullable=False)
    __table_args__ = (
        Index("ix_notif_user_created", "tenant_id", "user_id", "created_at"),
        Index("ix_notif_push_outbox", "push_status"),
    )


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = tenant_fk()
    notification_id: Mapped[str] = mapped_column(ID, ForeignKey("notifications.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    detail: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class NotificationPreference(Timestamped, Base):
    __tablename__ = "notification_preferences"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    events: Mapped[dict[str, bool]] = mapped_column(JSON, nullable=False, default=dict)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    quiet_start: Mapped[time | None] = mapped_column(Time)
    quiet_end: Mapped[time | None] = mapped_column(Time)
    reminder_offsets: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=lambda: [60])
    routine_sound: Mapped[str] = mapped_column(String(60), default="builtin:chime", nullable=False)
    urgent_sound: Mapped[str] = mapped_column(String(60), default="builtin:alert", nullable=False)
    routine_volume: Mapped[float] = mapped_column(default=0.7, nullable=False)
    urgent_volume: Mapped[float] = mapped_column(default=1.0, nullable=False)
    sound_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_pref_tenant_user"),)


class SoundFile(Base):
    __tablename__ = "sound_files"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    original_name: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(40), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_name: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(800), unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)
    user_agent: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class ReminderJob(Base):
    __tablename__ = "reminder_jobs"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    appointment_id: Mapped[str] = mapped_column(ID, ForeignKey("appointments.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="scheduled", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    __table_args__ = (
        # One reminder per appointment, recipient and offset: the core duplicate guard.
        UniqueConstraint("appointment_id", "user_id", "offset_minutes", name="uq_reminder_once"),
        Index("ix_reminder_due", "status", "due_at"),
        CheckConstraint("status in ('scheduled','processing','sent','failed','cancelled','skipped')", name="ck_reminder_status"),
    )


# ---------------------------------------------------------------- integrations, health, audit
class TelephonyIntegration(Timestamped, Base):
    __tablename__ = "telephony_integrations"
    id: Mapped[str] = pk()
    tenant_id: Mapped[str] = tenant_fk()
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="asterisk_audiosocket")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_configured")
    did_number: Mapped[str | None] = mapped_column(String(20), unique=True)  # E.164 number callers dial
    sip_host: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    sip_username: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    sip_password_enc: Mapped[str] = mapped_column(Text, default="", nullable=False)
    inbound_token_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    inbound_token_hint: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    forwarding_number: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_call_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    checklist: Mapped[dict[str, bool]] = mapped_column(JSON, nullable=False, default=dict)
    __table_args__ = (UniqueConstraint("tenant_id", "provider", name="uq_tel_tenant_provider"),)


class ServiceHealth(Base):
    __tablename__ = "service_health"
    component: Mapped[str] = mapped_column(String(30), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ready | missing | downloading | error | disabled
    detail: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str | None] = mapped_column(ID, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(ID, ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), default="", nullable=False)
    entity_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class AppSecret(Base):
    """Platform-level generated secrets (e.g. VAPID keypair), encrypted at rest."""

    __tablename__ = "app_secrets"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    value_enc: Mapped[str] = mapped_column(Text, nullable=False)
    public_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

"""Notification feed, preferences, custom sounds and Web Push subscriptions."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, new_id, utcnow
from ..deps import TenantContext, require_tenant
from ..errors import AppError, NotFound
from ..models import NOTIFICATION_KINDS, Notification, PushSubscription, SoundFile
from ..services import reminders
from ..services.delivery import email_configured, vapid_public_key
from ..services.notifications import get_prefs, notify
from .common import hhmm, notification_out, parse_hhmm

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

BUILTIN_SOUNDS = ["chime", "bell", "pulse", "alert", "soft"]
MAX_SOUND_BYTES = 1_000_000
MAX_SOUNDS_PER_USER = 10
SOUND_TYPES = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "m4a": "audio/mp4", "webm": "audio/webm"}


def sniff_audio(head: bytes) -> str | None:
    """Identify the audio container from magic bytes; never trust the browser-supplied type."""
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if head[:4] == b"OggS":
        return "ogg"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    return None


class PrefsIn(BaseModel):
    events: dict[str, bool]
    in_app_enabled: bool = True
    push_enabled: bool = True
    email_enabled: bool = False
    quiet_hours_enabled: bool = False
    quiet_start: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    reminder_offsets: list[int] = Field(default_factory=lambda: [60], max_length=5)
    routine_sound: str = "builtin:chime"
    urgent_sound: str = "builtin:alert"
    routine_volume: float = Field(0.7, ge=0, le=1)
    urgent_volume: float = Field(1.0, ge=0, le=1)
    sound_enabled: bool = True

    @field_validator("events")
    @classmethod
    def _events(cls, v):
        return {k: bool(val) for k, val in v.items() if k in NOTIFICATION_KINDS}

    @field_validator("reminder_offsets")
    @classmethod
    def _offsets(cls, v):
        for o in v:
            if not 1 <= o <= reminders.MAX_REMINDER_OFFSET:
                raise ValueError("Reminder offsets must be between 1 minute and 7 days.")
        return sorted(set(v))


class SubscribeIn(BaseModel):
    endpoint: str = Field(max_length=800, pattern=r"^https://")
    keys: dict[str, str]


def prefs_out(p, db: Session, ctx: TenantContext) -> dict:
    sounds = db.scalars(select(SoundFile).where(SoundFile.tenant_id == ctx.tenant_id, SoundFile.user_id == ctx.user.id)
                        .order_by(SoundFile.created_at)).all()
    return {
        "events": {k: p.events.get(k, True) for k in NOTIFICATION_KINDS},
        "in_app_enabled": p.in_app_enabled, "push_enabled": p.push_enabled, "email_enabled": p.email_enabled,
        "email_available": email_configured(),
        "quiet_hours_enabled": p.quiet_hours_enabled, "quiet_start": hhmm(p.quiet_start), "quiet_end": hhmm(p.quiet_end),
        "reminder_offsets": p.reminder_offsets, "routine_sound": p.routine_sound, "urgent_sound": p.urgent_sound,
        "routine_volume": p.routine_volume, "urgent_volume": p.urgent_volume, "sound_enabled": p.sound_enabled,
        "builtin_sounds": BUILTIN_SOUNDS,
        "custom_sounds": [{"id": s.id, "name": s.original_name, "size_bytes": s.size_bytes, "content_type": s.content_type} for s in sounds],
        "push_subscriptions": db.scalar(select(func.count()).select_from(PushSubscription).where(
            PushSubscription.user_id == ctx.user.id, PushSubscription.tenant_id == ctx.tenant_id)) or 0,
    }


@router.get("")
def feed(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db),
         unread_only: bool = False, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    stmt = select(Notification).where(Notification.tenant_id == ctx.tenant_id, Notification.user_id == ctx.user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = db.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)).all()
    unread = db.scalar(select(func.count()).select_from(Notification).where(
        Notification.tenant_id == ctx.tenant_id, Notification.user_id == ctx.user.id, Notification.read_at.is_(None))) or 0
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    return {"items": [notification_out(n) for n in rows], "unread": unread, "total": total}


@router.post("/{nid}/read")
def mark_read(nid: str, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if n is None or n.tenant_id != ctx.tenant_id or n.user_id != ctx.user.id:
        raise NotFound("Notification not found.")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return notification_out(n)


@router.post("/{nid}/unread")
def mark_unread(nid: str, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if n is None or n.tenant_id != ctx.tenant_id or n.user_id != ctx.user.id:
        raise NotFound("Notification not found.")
    n.read_at = None
    db.commit()
    return notification_out(n)


@router.post("/read-all")
def read_all(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.tenant_id == ctx.tenant_id, Notification.user_id == ctx.user.id,
                                          Notification.read_at.is_(None)).values(read_at=utcnow()))
    db.commit()
    return {"ok": True}


@router.get("/preferences")
def get_preferences(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    p = get_prefs(db, ctx.tenant_id, ctx.user.id)
    db.commit()
    return prefs_out(p, db, ctx)


def _valid_sound(ref: str, db: Session, ctx: TenantContext) -> bool:
    if ref.startswith("builtin:"):
        return ref.split(":", 1)[1] in BUILTIN_SOUNDS
    if ref.startswith("upload:"):
        s = db.get(SoundFile, ref.split(":", 1)[1])
        return s is not None and s.tenant_id == ctx.tenant_id and s.user_id == ctx.user.id
    return False


@router.put("/preferences")
def put_preferences(body: PrefsIn, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    for ref in (body.routine_sound, body.urgent_sound):
        if not _valid_sound(ref, db, ctx):
            raise AppError("Selected sound is not available.", code="invalid_sound")
    if body.quiet_hours_enabled and (not body.quiet_start or not body.quiet_end or body.quiet_start == body.quiet_end):
        raise AppError("Set different start and end times for quiet hours.", code="invalid_quiet_hours")
    p = get_prefs(db, ctx.tenant_id, ctx.user.id)
    offsets_changed = sorted(p.reminder_offsets) != body.reminder_offsets or p.events.get("appointment_reminder", True) != body.events.get("appointment_reminder", True)
    data = body.model_dump()
    data["quiet_start"], data["quiet_end"] = parse_hhmm(body.quiet_start), parse_hhmm(body.quiet_end)
    data["events"] = {**{k: True for k in NOTIFICATION_KINDS}, **body.events}
    for k, v in data.items():
        setattr(p, k, v)
    db.flush()
    if offsets_changed:
        reminders.resync_user(db, ctx.tenant_id, ctx.user.id)
    db.commit()
    return prefs_out(p, db, ctx)


@router.post("/test")
def send_test(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db),
              urgent: bool = False):
    kind = "urgent_request" if urgent else "appointment_reminder"
    notify(db, ctx.tenant, kind, "Test notification" + (" (urgent)" if urgent else ""),
           "This is a test from your AI receptionist dashboard.", link="/notifications", user_ids=[ctx.user.id])
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ custom sounds
@router.post("/sounds", status_code=201)
async def upload_sound(file: UploadFile = File(...), ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    count = db.scalar(select(func.count()).select_from(SoundFile).where(SoundFile.user_id == ctx.user.id, SoundFile.tenant_id == ctx.tenant_id)) or 0
    if count >= MAX_SOUNDS_PER_USER:
        raise AppError(f"You can store up to {MAX_SOUNDS_PER_USER} custom sounds. Delete one first.", code="too_many_sounds")
    raw = await file.read(MAX_SOUND_BYTES + 1)
    if len(raw) > MAX_SOUND_BYTES:
        raise AppError("Sound files must be 1 MB or smaller.", code="file_too_large")
    if len(raw) < 64:
        raise AppError("This file is too small to be an audio file.", code="invalid_file")
    kind = sniff_audio(raw[:16])
    if kind is None:
        raise AppError("Unsupported audio format. Use WAV, MP3, OGG, M4A or WebM.", code="invalid_file")
    storage = f"{new_id()}.{kind}"
    folder = get_settings().data_dir / "uploads" / ctx.tenant_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / storage).write_bytes(raw)
    name = "".join(ch for ch in (file.filename or "sound") if ch.isalnum() or ch in " ._-")[:120] or "sound"
    s = SoundFile(tenant_id=ctx.tenant_id, user_id=ctx.user.id, original_name=name, content_type=SOUND_TYPES[kind],
                  size_bytes=len(raw), storage_name=storage)
    db.add(s)
    db.commit()
    return {"id": s.id, "name": s.original_name, "size_bytes": s.size_bytes, "content_type": s.content_type}


def _sound(db: Session, ctx: TenantContext, sid: str) -> SoundFile:
    s = db.get(SoundFile, sid)
    if s is None or s.tenant_id != ctx.tenant_id or s.user_id != ctx.user.id:
        raise NotFound("Sound not found.")
    return s


@router.get("/sounds/{sid}")
def get_sound(sid: str, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    s = _sound(db, ctx, sid)
    path = get_settings().data_dir / "uploads" / ctx.tenant_id / s.storage_name
    if not path.exists():
        raise NotFound("Sound file missing.")
    return FileResponse(path, media_type=s.content_type,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400",
                                 "Content-Disposition": "inline"})


@router.delete("/sounds/{sid}")
def delete_sound(sid: str, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    s = _sound(db, ctx, sid)
    (get_settings().data_dir / "uploads" / ctx.tenant_id / s.storage_name).unlink(missing_ok=True)
    p = get_prefs(db, ctx.tenant_id, ctx.user.id)
    ref = f"upload:{s.id}"
    if p.routine_sound == ref:
        p.routine_sound = "builtin:chime"
    if p.urgent_sound == ref:
        p.urgent_sound = "builtin:alert"
    db.delete(s)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ web push
@router.get("/push/key")
def push_key(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    return {"public_key": vapid_public_key(db)}


@router.post("/push/subscribe", status_code=201)
def subscribe(body: SubscribeIn, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    p256dh, auth = body.keys.get("p256dh", ""), body.keys.get("auth", "")
    if not p256dh or not auth or len(p256dh) > 200 or len(auth) > 100:
        raise AppError("Invalid push subscription.", code="invalid_subscription")
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if sub is None:
        sub = PushSubscription(endpoint=body.endpoint, tenant_id=ctx.tenant_id, user_id=ctx.user.id, p256dh=p256dh, auth=auth)
        db.add(sub)
    else:  # endpoint re-registered by (possibly) another user on the same browser
        sub.tenant_id, sub.user_id, sub.p256dh, sub.auth, sub.failure_count = ctx.tenant_id, ctx.user.id, p256dh, auth, 0
    db.commit()
    return {"ok": True}


@router.post("/push/unsubscribe")
def unsubscribe(body: SubscribeIn, ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == ctx.user.id))
    if sub:
        db.delete(sub)
        db.commit()
    return {"ok": True}

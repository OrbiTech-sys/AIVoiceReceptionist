"""Phone numbers, and the callbacks Asterisk makes during a call.

All businesses share one Asterisk and one SIP account owned by the platform. A platform admin assigns each business a
phone number, and the dialled number on an incoming call says which business it's for.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..deps import AuthContext, TenantContext, client_ip, require_platform_admin, require_tenant
from ..errors import AppError, Conflict, NotFound, Unauthorized
from ..models import Call, ReceptionistSettings, TelephonyIntegration, Tenant
from ..ratelimit import limiter
from ..security import constant_eq, dialled_number_candidates, format_phone, normalize_phone
from ..services.audit import audit
from ..services.notifications import notify
from ..telephony import base, health
from ..telephony.audiosocket import PendingCall, registry
from ..voice import runtime

log = logging.getLogger("voicerep.telephony")
router = APIRouter(prefix="/api/telephony", tags=["telephony"])
PROVIDER = "asterisk_audiosocket"
UUID_PATTERN = r"^[0-9a-fA-F-]{36}$"
REFUSAL_DETAIL = {
    "off": "the receptionist is turned off",
    "unavailable": "the receptionist's speech models aren't ready",
    "busy": "the receptionist was already on as many calls as it can handle",
}


def _integration(db: Session, tenant_id: str) -> TelephonyIntegration | None:
    return db.scalar(select(TelephonyIntegration).where(TelephonyIntegration.tenant_id == tenant_id,
                                                        TelephonyIntegration.provider == PROVIDER))


def integration_out(i: TelephonyIntegration | None) -> dict:
    if i is None:
        return {"configured": False, "status": "not_configured"}
    return {
        "configured": True, "status": i.status, "did_number": i.did_number, "did_display": format_phone(i.did_number),
        "last_validated_at": i.last_validated_at.isoformat() if i.last_validated_at else None,
        "last_call_at": i.last_call_at.isoformat() if i.last_call_at else None, "last_error": i.last_error,
    }


@router.get("")
def get_integration(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    return integration_out(_integration(db, ctx.tenant_id))


# ------------------------------------------------------------------ assigning numbers (platform admin)
class AssignIn(BaseModel):
    did_number: str = Field(min_length=4, max_length=32)


@router.put("/admin/tenants/{tenant_id}")
def assign_number(tenant_id: str, body: AssignIn, ctx: AuthContext = Depends(require_platform_admin), db: Session = Depends(get_db)):
    if db.get(Tenant, tenant_id) is None:
        raise NotFound("Unknown business.")
    did = normalize_phone(body.did_number)
    if not did:
        raise AppError("Enter a valid phone number, for example +1 415 555 0100.", code="invalid_phone")
    i = _integration(db, tenant_id) or TelephonyIntegration(tenant_id=tenant_id, provider=PROVIDER, checklist={})
    if i.did_number != did:
        i.did_number = did
        i.status = "pending_validation"  # never "connected" until a real call has reached this number
        i.last_validated_at = None
        i.last_error = ""
    db.add(i)
    try:
        audit(db, "telephony.number_assigned", tenant_id=tenant_id, user_id=ctx.user.id, entity_type="telephony")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("This phone number is already assigned to another business.", code="did_taken")
    return integration_out(i)


@router.delete("/admin/tenants/{tenant_id}")
def remove_number(tenant_id: str, ctx: AuthContext = Depends(require_platform_admin), db: Session = Depends(get_db)):
    i = _integration(db, tenant_id)
    if i is not None:
        db.delete(i)
        audit(db, "telephony.number_removed", tenant_id=tenant_id, user_id=ctx.user.id, entity_type="telephony")
        db.commit()
    return integration_out(None)


# ------------------------------------------------------------------ callbacks from Asterisk's dialplan
def _authorize_dialplan(request: Request, token: str) -> None:
    limiter.check(f"asterisk:{client_ip(request)}", 300, 60)
    expected = get_settings().asterisk_token
    if not expected or not constant_eq(expected, token):
        raise Unauthorized("invalid token")


def _integration_for_dialled_number(db: Session, did: str) -> TelephonyIntegration | None:
    for number in dialled_number_candidates(did):
        i = db.scalar(select(TelephonyIntegration).where(TelephonyIntegration.did_number == number))
        if i is not None:
            return i
    return None


def _calls_in_progress() -> int:
    adapter = base.get(PROVIDER)
    return registry.pending_count() + (adapter.status()["active_calls"] if adapter else 0)


def _refuse(db: Session, i: TelephonyIntegration, settings: ReceptionistSettings | None, reason: str, uuid: str, caller: str) -> str:
    """Hand a call the receptionist can't take to the business's transfer number, and make sure it isn't lost silently."""
    number = settings.transfer_number if settings else ""
    detail = REFUSAL_DETAIL[reason]
    call = Call(tenant_id=i.tenant_id, channel="phone", external_id=uuid.lower(), caller_number=normalize_phone(caller),
                status="completed", ended_at=utcnow(), duration_sec=0)
    if number:
        call.outcome = "transferred"
        call.summary = f"Forwarded to the transfer number because {detail}."
    else:
        call.outcome = "missed"
        call.follow_up_status = "needed"
        call.summary = f"Not answered because {detail}."
    db.add(call)
    db.flush()
    if not number:
        tenant = db.get(Tenant, i.tenant_id)
        who = call.caller_number or "Unknown caller"
        notify(db, tenant, "call_missed", f"Missed call from {who}", call.summary,
               link=f"/calls?id={call.id}", entity_type="call", entity_id=call.id)
    db.commit()
    return f"forward:{number}" if number else reason


@router.post("/asterisk/precall", response_class=PlainTextResponse)
def precall(request: Request, uuid: str = Form(..., pattern=UUID_PATTERN), token: str = Form(..., max_length=200),
            did: str = Form(..., max_length=32), caller: str = Form("", max_length=32), db: Session = Depends(get_db)):
    """Asterisk asks this before answering a call. Returns "ok" to take it, or "forward:<number>" to send it to the
    business's transfer number. Anything else means nobody can take it: a phone caller has no screen to show an error
    on, so a call the receptionist can't serve would otherwise just be silence."""
    _authorize_dialplan(request, token)
    i = _integration_for_dialled_number(db, did)
    if i is None:
        log.warning("call to a number no business has: %r", did)
        return "unknown"
    settings = db.get(ReceptionistSettings, i.tenant_id)
    if settings is not None and not settings.enabled:
        return _refuse(db, i, settings, "off", uuid, caller)
    if not runtime.voice_ready():
        log.warning("refused a call for tenant %s: speech models are not ready", i.tenant_id)
        return _refuse(db, i, settings, "unavailable", uuid, caller)
    if _calls_in_progress() >= get_settings().max_concurrent_calls:
        log.warning("refused a call for tenant %s: at the limit of %d calls", i.tenant_id, get_settings().max_concurrent_calls)
        return _refuse(db, i, settings, "busy", uuid, caller)
    registry.announce(uuid, PendingCall(i.tenant_id, i.id, caller or None, i.did_number, time.monotonic()))
    i.last_call_at = utcnow()
    db.commit()
    return "ok"


@router.post("/asterisk/next-action", response_class=PlainTextResponse)
def next_action(request: Request, uuid: str = Form(..., pattern=UUID_PATTERN), token: str = Form(..., max_length=200)):
    _authorize_dialplan(request, token)
    return registry.action(uuid)


@router.post("/asterisk/transfer-failed", response_class=PlainTextResponse)
def transfer_failed(request: Request, uuid: str = Form(..., pattern=UUID_PATTERN), token: str = Form(..., max_length=200),
                    db: Session = Depends(get_db)):
    """Nobody picked up after the receptionist transferred a caller, so someone needs to call them back."""
    _authorize_dialplan(request, token)
    call = db.scalar(select(Call).where(Call.external_id == uuid.lower()))
    if call is None:
        return "unknown"
    call.outcome = "answered"
    call.follow_up_status = "needed"
    call.summary = f"{call.summary} The transfer wasn't answered.".strip()
    who = call.caller_name or call.caller_number or "Unknown caller"
    notify(db, db.get(Tenant, call.tenant_id), "callback_requested", f"Call back: {who}",
           "They asked for a person, but nobody answered the transfer.",
           link=f"/calls?id={call.id}", entity_type="call", entity_id=call.id)
    db.commit()
    return "ok"


@router.post("/asterisk/heartbeat", response_class=PlainTextResponse)
def heartbeat(request: Request, token: str = Form(..., max_length=200), db: Session = Depends(get_db)):
    _authorize_dialplan(request, token)
    health.record_heartbeat(db)
    return "ok"

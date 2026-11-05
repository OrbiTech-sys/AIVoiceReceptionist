"""Live phone configuration (Asterisk + SIP trunk) and the dialplan callback endpoints."""
from __future__ import annotations

import logging
import re
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..deps import TenantContext, client_ip, require_owner, require_tenant
from ..errors import AppError, Conflict, Unauthorized
from ..models import ReceptionistSettings, TelephonyIntegration
from ..ratelimit import limiter
from ..security import constant_eq, dialled_number_candidates, encrypt_secret, new_token, normalize_phone, token_hash
from ..services.audit import audit
from ..telephony import base
from ..telephony.audiosocket import PendingCall, registry
from ..voice import runtime

log = logging.getLogger("voicerep.telephony")
router = APIRouter(prefix="/api/telephony", tags=["telephony"])
PROVIDER = "asterisk_audiosocket"
HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,200}(:\d{1,5})?$")
CHECKLIST = ("sip_account", "asterisk_installed", "dialplan_loaded", "forwarding_enabled", "test_call_received")


class ConfigIn(BaseModel):
    did_number: str = Field(min_length=4, max_length=32)
    sip_host: str = Field(min_length=3, max_length=200)
    sip_username: str = Field(min_length=1, max_length=120)
    sip_password: str | None = Field(None, max_length=200)  # omit to keep the stored secret
    forwarding_number: str = Field("", max_length=32)


class ChecklistIn(BaseModel):
    items: dict[str, bool]


def _integration(db: Session, tenant_id: str) -> TelephonyIntegration | None:
    return db.scalar(select(TelephonyIntegration).where(TelephonyIntegration.tenant_id == tenant_id,
                                                        TelephonyIntegration.provider == PROVIDER))


def integration_out(i: TelephonyIntegration | None) -> dict:
    adapter = base.get(PROVIDER)
    listener = adapter.status() if adapter else {"listening": False, "error": "adapter disabled"}
    if i is None:
        return {"configured": False, "status": "not_configured", "listener": listener,
                "checklist": {k: False for k in CHECKLIST}}
    return {
        "configured": True, "status": i.status, "provider": i.provider, "did_number": i.did_number,
        "sip_host": i.sip_host, "sip_username": i.sip_username, "has_password": bool(i.sip_password_enc),
        "forwarding_number": i.forwarding_number, "inbound_token_hint": i.inbound_token_hint,
        "last_validated_at": i.last_validated_at.isoformat() if i.last_validated_at else None,
        "last_call_at": i.last_call_at.isoformat() if i.last_call_at else None, "last_error": i.last_error,
        "checklist": {k: bool((i.checklist or {}).get(k)) for k in CHECKLIST}, "listener": listener,
    }


@router.get("")
def get_integration(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    return integration_out(_integration(db, ctx.tenant_id))


@router.put("")
def put_integration(body: ConfigIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    did = normalize_phone(body.did_number)
    if not did:
        raise AppError("Enter the phone number in international format, e.g. +1 415 555 0100.", code="invalid_phone")
    if not HOST_RE.match(body.sip_host):
        raise AppError("SIP host must be a hostname or IP address, optionally with :port.", code="invalid_host")
    fwd = normalize_phone(body.forwarding_number) if body.forwarding_number else ""
    if body.forwarding_number and not fwd:
        raise AppError("Existing business number is not valid.", code="invalid_phone")
    i = _integration(db, ctx.tenant_id)
    token = None
    if i is None:
        i = TelephonyIntegration(tenant_id=ctx.tenant_id, provider=PROVIDER, checklist={})
        db.add(i)
        token = new_token()
        i.inbound_token_hash, i.inbound_token_hint = token_hash(token), token[:6]
    changed = (i.did_number != did or i.sip_host != body.sip_host or i.sip_username != body.sip_username
               or body.sip_password is not None)
    i.did_number, i.sip_host, i.sip_username, i.forwarding_number = did, body.sip_host.strip(), body.sip_username.strip(), fwd or ""
    if body.sip_password is not None:
        i.sip_password_enc = encrypt_secret(body.sip_password)
    if not i.sip_password_enc:
        raise AppError("Enter the SIP password from your provider.", code="password_required")
    if changed or i.status == "not_configured":
        # Never claim "connected" until a real call has arrived with this configuration.
        i.status = "pending_validation"
        i.checklist = {**(i.checklist or {}), "test_call_received": False}
    try:
        audit(db, "telephony.configured", tenant_id=ctx.tenant_id, user_id=ctx.user.id, entity_type="telephony")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("This phone number is already connected to another business.", code="did_taken")
    out = integration_out(i)
    if token:
        out["inbound_token"] = token  # shown once
    return out


@router.post("/rotate-token")
def rotate_token(ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    i = _integration(db, ctx.tenant_id)
    if i is None:
        raise AppError("Save the phone configuration first.", code="not_configured")
    token = new_token()
    i.inbound_token_hash, i.inbound_token_hint = token_hash(token), token[:6]
    i.status = "pending_validation"
    audit(db, "telephony.token_rotated", tenant_id=ctx.tenant_id, user_id=ctx.user.id, entity_type="telephony")
    db.commit()
    return {**integration_out(i), "inbound_token": token}


@router.put("/checklist")
def put_checklist(body: ChecklistIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    i = _integration(db, ctx.tenant_id)
    if i is None:
        raise AppError("Save the phone configuration first.", code="not_configured")
    # test_call_received can only be set by a real call.
    items = {k: bool(v) for k, v in body.items.items() if k in CHECKLIST and k != "test_call_received"}
    i.checklist = {**(i.checklist or {}), **items}
    db.commit()
    return integration_out(i)


@router.post("/validate")
def validate(ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    i = _integration(db, ctx.tenant_id)
    if i is None:
        raise AppError("Save the phone configuration first.", code="not_configured")
    adapter = base.get(PROVIDER)
    problems = []
    if adapter is None or not adapter.status()["listening"]:
        problems.append("The AudioSocket listener is not running on the server.")
    if not i.sip_password_enc:
        problems.append("SIP password is missing.")
    if i.status != "connected":
        problems.append("No test call has reached the receptionist yet. Call your number to finish validation.")
    i.last_error = " ".join(problems)[:300]
    if problems and i.status == "connected" and adapter and not adapter.status()["listening"]:
        i.status = "error"
    db.commit()
    return {**integration_out(i), "problems": problems}


@router.delete("")
def disconnect(ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    i = _integration(db, ctx.tenant_id)
    if i is not None:
        db.delete(i)
        audit(db, "telephony.disconnected", tenant_id=ctx.tenant_id, user_id=ctx.user.id, entity_type="telephony")
        db.commit()
    return integration_out(None)


@router.get("/asterisk-config", response_class=PlainTextResponse)
def asterisk_config(ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db),
                    api_host: str = Query("127.0.0.1:8000", max_length=200),
                    audiosocket_host: str = Query("127.0.0.1", max_length=200)):
    """Generate pjsip.conf / extensions.conf snippets. The SIP password is NOT included."""
    i = _integration(db, ctx.tenant_id)
    if i is None:
        raise AppError("Save the phone configuration first.", code="not_configured")
    if not HOST_RE.match(api_host) or not HOST_RE.match(audiosocket_host):
        raise AppError("Invalid host.", code="invalid_host")
    port = get_settings().audiosocket_port
    t = f"t{ctx.tenant_id[:8]}"
    host = i.sip_host
    return f"""; ===== pjsip.conf (append) =====
; SIP trunk for {ctx.tenant.name}. Replace <SIP_PASSWORD> with the password from your provider.
[{t}-trunk]
type=registration
outbound_auth={t}-auth
server_uri=sip:{host}
client_uri=sip:{i.sip_username}@{host}

[{t}-auth]
type=auth
auth_type=userpass
username={i.sip_username}
password=<SIP_PASSWORD>

[{t}-aor]
type=aor
contact=sip:{host}

[{t}-endpoint]
type=endpoint
context=voicerep-inbound
disallow=all
allow=ulaw,alaw
outbound_auth={t}-auth
aors={t}-aor
direct_media=no

[{t}-identify]
type=identify
endpoint={t}-endpoint
match={host.split(':')[0]}

; ===== extensions.conf (append) =====
; Requires app_audiosocket and func_curl (both ship with Asterisk 18+).
[voicerep-inbound]
exten => {i.did_number},1,Answer()
 same => n,Set(VR_UUID=${{SHELL(cat /proc/sys/kernel/random/uuid | tr -d '\\n')}})
 same => n,Set(VR_OK=${{CURL(http://{api_host}/api/telephony/asterisk/precall?uuid=${{VR_UUID}}&did=${{URIENCODE(${{EXTEN}})}}&caller=${{URIENCODE(${{CALLERID(num)}})}}&token=<INBOUND_TOKEN>)}})
 same => n,GotoIf($["${{VR_OK}}" != "ok"]?failed)
 same => n,AudioSocket(${{VR_UUID}},{audiosocket_host}:{port})
 same => n,Set(VR_NEXT=${{CURL(http://{api_host}/api/telephony/asterisk/next-action?uuid=${{VR_UUID}}&token=<INBOUND_TOKEN>)}})
 same => n,GotoIf($["${{VR_NEXT:0:9}}" = "transfer:"]?transfer)
 same => n,Hangup()
 same => n(transfer),Dial(PJSIP/${{VR_NEXT:9}}@{t}-endpoint,30)
 same => n,Hangup()
 same => n(failed),Playback(ss-noservice)
 same => n,Hangup()
"""


def _authorize_dialplan(db: Session, token: str, did: str | None) -> TelephonyIntegration:
    th = token_hash(token)
    if did:
        match = TelephonyIntegration.did_number.in_(dialled_number_candidates(did))
    else:
        match = TelephonyIntegration.inbound_token_hash == th
    for i in db.scalars(select(TelephonyIntegration).where(match)):
        if constant_eq(i.inbound_token_hash, th):
            return i
    raise Unauthorized("invalid token")


@router.get("/asterisk/precall", response_class=PlainTextResponse)
def precall(request: Request, uuid: str = Query(..., pattern=r"^[0-9a-fA-F-]{36}$"), token: str = Query(..., max_length=100),
            did: str = Query(..., max_length=32), caller: str = Query("", max_length=32), db: Session = Depends(get_db)):
    """Called by the Asterisk dialplan before handing audio to AudioSocket.

    Returns "ok" to take the call. Anything else ("off", "unavailable") means the receptionist must not answer:
    a phone caller has no screen to show an error on, so a call it can't serve would just be silence."""
    limiter.check(f"precall:{client_ip(request)}", 120, 60)
    i = _authorize_dialplan(db, token, did)
    settings = db.get(ReceptionistSettings, i.tenant_id)
    if settings is not None and not settings.enabled:
        return "off"
    if not runtime.voice_ready():
        log.warning("refused a call for tenant %s: speech models are not ready", i.tenant_id)
        return "unavailable"
    registry.announce(uuid, PendingCall(i.tenant_id, i.id, caller or None, i.did_number, time.monotonic()))
    i.last_call_at = utcnow()
    db.commit()
    return "ok"


@router.get("/asterisk/next-action", response_class=PlainTextResponse)
def next_action(request: Request, uuid: str = Query(..., pattern=r"^[0-9a-fA-F-]{36}$"), token: str = Query(..., max_length=100),
                db: Session = Depends(get_db)):
    limiter.check(f"precall:{client_ip(request)}", 120, 60)
    _authorize_dialplan(db, token, None)
    return registry.action(uuid)

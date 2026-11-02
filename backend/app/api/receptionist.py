"""Receptionist configuration, voice previews and the browser demo WebSocket."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import wave

import numpy as np
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal, get_db
from ..deps import SESSION_COOKIE, TenantContext, load_session, require_owner, require_tenant
from ..errors import AppError
from ..models import Membership, ReceptionistSettings
from ..ratelimit import limiter
from ..voice import runtime
from ..voice.engines import KOKORO_VOICES, float_to_pcm16, pcm16_to_float
from ..voice.session import CallSession

log = logging.getLogger("voicerep.ws")
router = APIRouter(prefix="/api/receptionist", tags=["receptionist"])
_active_demo: dict[str, int] = {}


class SettingsIn(BaseModel):
    enabled: bool = True
    name: str = Field(min_length=1, max_length=60)
    voice_id: str
    language: str = "en-US"
    style: str = Field(pattern="^(formal|warm|friendly|professional)$")
    speed: float = Field(ge=0.8, le=1.25)
    greeting: str = Field("", max_length=600)
    closing: str = Field("", max_length=400)
    transfer_number: str = Field("", max_length=32)
    escalation_contact: str = Field("", max_length=200)
    boundaries: str = Field("", max_length=2000)


def settings_out(s: ReceptionistSettings) -> dict:
    return {k: getattr(s, k) for k in SettingsIn.model_fields}


@router.get("")
def get_settings_api(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    s = db.get(ReceptionistSettings, ctx.tenant_id)
    if s is None:
        s = ReceptionistSettings(tenant_id=ctx.tenant_id)
        db.add(s)
        db.commit()
    return {**settings_out(s), "voices": runtime.available_voices(), "readiness": runtime.readiness()}


@router.put("")
def put_settings(body: SettingsIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    if body.voice_id not in {v.id for v in KOKORO_VOICES}:
        raise AppError("Unknown voice.", code="invalid_voice")
    if body.language not in {v.language for v in KOKORO_VOICES}:
        raise AppError("That language is not supported by the installed models.", code="invalid_language")
    if body.transfer_number:
        from ..security import normalize_phone

        e164 = normalize_phone(body.transfer_number)
        if not e164:
            raise AppError("Transfer number is not a valid phone number.", code="invalid_phone")
        body.transfer_number = e164
    s = db.get(ReceptionistSettings, ctx.tenant_id) or ReceptionistSettings(tenant_id=ctx.tenant_id)
    for k, v in body.model_dump().items():
        setattr(s, k, v.strip() if isinstance(v, str) else v)
    db.add(s)
    db.commit()
    return settings_out(s)


@router.get("/readiness")
def readiness(ctx: TenantContext = Depends(require_tenant)):
    return runtime.readiness()


@router.get("/voices/{voice_id}/preview")
async def preview(voice_id: str, text: str = Query("", max_length=200), speed: float = Query(1.0, ge=0.8, le=1.25),
                  ctx: TenantContext = Depends(require_tenant)):
    limiter.check(f"preview:{ctx.tenant_id}", 30, 60)
    if voice_id not in {v.id for v in KOKORO_VOICES}:
        raise AppError("Unknown voice.", code="invalid_voice")
    tts = await asyncio.get_running_loop().run_in_executor(None, runtime.get_tts)
    if tts is None:
        raise AppError("The voice model is not installed yet. Ask your administrator to install it.", code="tts_missing",
                       status_code=503)
    phrase = text.strip() or f"Hi, thanks for calling {ctx.tenant.name}. How can I help you today?"
    audio = await asyncio.get_running_loop().run_in_executor(None, tts.synthesize, phrase, voice_id, speed)
    getattr(tts, "verified", set()).add(voice_id)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tts.sample_rate)
        w.writeframes(float_to_pcm16(audio))
    return Response(buf.getvalue(), media_type="audio/wav", headers={"Cache-Control": "no-store"})


class BrowserTransport:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self._send_lock = asyncio.Lock()

    async def send_event(self, event: dict) -> None:
        async with self._send_lock:
            await self.ws.send_text(json.dumps(event))

    async def send_audio(self, audio: np.ndarray, sample_rate: int) -> None:
        async with self._send_lock:
            await self.ws.send_text(json.dumps({"type": "audio", "sample_rate": sample_rate, "samples": int(audio.size)}))
            await self.ws.send_bytes(float_to_pcm16(audio))

    async def stop_audio(self) -> None:
        await self.send_event({"type": "stop_audio"})


@router.websocket("/ws")
async def demo_ws(ws: WebSocket):
    """Browser demo call. Client sends 16 kHz mono PCM16 binary frames, or JSON {type:"text"} for typed input."""
    origin = ws.headers.get("origin", "")
    if origin not in get_settings().origins:
        await ws.close(code=4403)
        return
    with SessionLocal() as db:
        sess = load_session(db, ws.cookies.get(SESSION_COOKIE))
        tenant_id = sess.active_tenant_id if sess else None
        ok = bool(sess and tenant_id and db.scalar(select(Membership.id).where(
            Membership.user_id == sess.user_id, Membership.tenant_id == tenant_id)))
    if not ok:
        await ws.close(code=4401)
        return
    if _active_demo.get(tenant_id, 0) >= 3:
        await ws.close(code=4429)
        return
    await ws.accept()
    _active_demo[tenant_id] = _active_demo.get(tenant_id, 0) + 1
    transport = BrowserTransport(ws)
    call = CallSession(tenant_id, transport, channel="browser_demo", is_demo=True)
    reason = "hangup"
    try:
        await call.start()
        end_wait = asyncio.create_task(call.end_requested.wait())
        while True:
            recv = asyncio.create_task(ws.receive())
            done, _ = await asyncio.wait({recv, end_wait}, return_when=asyncio.FIRST_COMPLETED)
            if end_wait in done:
                recv.cancel()
                break
            msg = recv.result()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                data = msg["bytes"]
                if len(data) % 2 == 0 and len(data) <= 32000:
                    await call.on_audio(pcm16_to_float(data), 16000)
            elif msg.get("text"):
                try:
                    payload = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                kind = payload.get("type")
                if kind == "text" and isinstance(payload.get("text"), str):
                    limiter.check(f"demo-text:{tenant_id}", 60, 60)
                    asyncio.create_task(call.on_text(payload["text"]))
                elif kind == "interrupt":
                    await call.interrupt()
                elif kind == "hangup":
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.exception("demo websocket error")
        call._failed = f"{type(exc).__name__}"
        reason = "error"
    finally:
        _active_demo[tenant_id] = max(0, _active_demo.get(tenant_id, 1) - 1)
        await call.close(reason)
        try:
            await ws.close()
        except Exception:
            pass


@router.get("/status")
def receptionist_status(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    """Single source of truth for the persistent Online / Offline / Setup required indicator."""
    from ..models import TelephonyIntegration
    from ..telephony import base

    s = db.get(ReceptionistSettings, ctx.tenant_id)
    r = runtime.readiness()
    tel = db.scalar(select(TelephonyIntegration).where(TelephonyIntegration.tenant_id == ctx.tenant_id))
    adapter = base.get("asterisk_audiosocket")
    listening = bool(adapter and adapter.status()["listening"])
    if not ctx.tenant.onboarding_completed:
        state, detail = "setup_required", "Finish onboarding to activate your receptionist."
    elif not r["voice_ready"]:
        state, detail = "setup_required", "Speech models are not installed on the server yet."
    elif s is not None and not s.enabled:
        state, detail = "offline", "The receptionist is turned off."
    elif tel is not None and tel.status == "connected" and listening:
        state, detail = "online", "Answering phone calls."
    elif tel is not None and (tel.status == "error" or (tel.status == "connected" and not listening)):
        state, detail = "offline", tel.last_error or "The phone connection is down."
    else:
        state, detail = "demo", "Ready for browser testing. No phone line connected yet."
    return {"state": state, "detail": detail, "voice_ready": r["voice_ready"],
            "phone_status": tel.status if tel else "not_configured"}

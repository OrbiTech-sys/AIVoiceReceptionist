"""Health checks, model readiness and platform administration."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import AuthContext, require_platform_admin, require_tenant
from ..errors import AppError, NotFound
from ..models import Appointment, Call, Membership, Tenant
from ..services.audit import audit
from ..services.delivery import email_configured
from ..telephony import base
from ..voice import models_manager as mm
from ..voice import runtime

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}


@router.get("/system/status")
def system_status(_ctx=Depends(require_tenant)):
    r = runtime.readiness()
    return {**r, "telephony": base.all_status(), "email_configured": email_configured()}


@router.get("/admin/models")
def admin_models(_ctx: AuthContext = Depends(require_platform_admin)):
    return {"models": mm.status(), "hardware": mm.hardware(), "readiness": runtime.readiness()}


@router.post("/admin/models/{model_id}/install")
def admin_install(model_id: str, ctx: AuthContext = Depends(require_platform_admin), db: Session = Depends(get_db)):
    if model_id not in mm.CATALOG:
        raise NotFound("Unknown model.")
    st = next(s for s in mm.status() if s["id"] == model_id)
    if not st["compatible"]:
        raise AppError(f"This server has less RAM than the {st['min_ram_gb']} GB this model needs.", code="incompatible")
    mm.install(model_id)
    audit(db, "admin.model_install", user_id=ctx.user.id, entity_type="model", entity_id=model_id[:32])
    db.commit()
    return {"ok": True}


@router.delete("/admin/models/{model_id}")
def admin_remove(model_id: str, ctx: AuthContext = Depends(require_platform_admin), db: Session = Depends(get_db)):
    if model_id not in mm.CATALOG:
        raise NotFound("Unknown model.")
    mm.remove(model_id)
    audit(db, "admin.model_removed", user_id=ctx.user.id, entity_type="model", entity_id=model_id[:32])
    db.commit()
    return {"ok": True}


@router.post("/admin/models/warmup")
async def admin_warmup(_ctx: AuthContext = Depends(require_platform_admin)):
    res = await asyncio.get_running_loop().run_in_executor(None, runtime.warmup)
    return {"latency": res, "readiness": runtime.readiness()}


@router.get("/admin/tenants")
def admin_tenants(_ctx: AuthContext = Depends(require_platform_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(Tenant).order_by(Tenant.created_at.desc()).limit(200)).all()
    out = []
    for t in rows:
        out.append({
            "id": t.id, "name": t.name, "business_type": t.business_type, "created_at": t.created_at.isoformat(),
            "onboarding_completed": t.onboarding_completed, "is_demo": t.is_demo,
            "members": db.scalar(select(func.count()).select_from(Membership).where(Membership.tenant_id == t.id)),
            "calls": db.scalar(select(func.count()).select_from(Call).where(Call.tenant_id == t.id)),
            "appointments": db.scalar(select(func.count()).select_from(Appointment).where(Appointment.tenant_id == t.id)),
        })
    return out

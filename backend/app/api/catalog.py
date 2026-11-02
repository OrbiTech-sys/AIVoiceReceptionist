"""Services, staff (with availability) and knowledge base, including CSV/TXT import."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import TenantContext, require_owner, require_tenant
from ..errors import AppError, Conflict, NotFound
from ..models import Appointment, KnowledgeEntry, Service, StaffAvailability, StaffMember, StaffService
from .common import parse_hhmm, service_out, staff_out

router = APIRouter(prefix="/api", tags=["catalog"])
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=1000)
    duration_min: int = Field(ge=5, le=600)
    buffer_min: int | None = Field(None, ge=0, le=240)
    price_text: str = Field("", max_length=80)
    is_active: bool = True


class AvailIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start: str = Field(pattern=HHMM)
    end: str = Field(pattern=HHMM)


class StaffIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    title: str = Field("", max_length=120)
    email: str = Field("", max_length=254)
    is_active: bool = True
    service_ids: list[str] = []
    availability: list[AvailIn] = []


class KnowledgeIn(BaseModel):
    kind: str = Field("faq", pattern="^(faq|policy|directions|general)$")
    question: str = Field(min_length=2, max_length=300)
    answer: str = Field(min_length=1, max_length=2000)
    is_active: bool = True


# ------------------------------------------------------------------ services
@router.get("/services")
def list_services(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    rows = db.scalars(select(Service).where(Service.tenant_id == ctx.tenant_id).order_by(Service.name)).all()
    return [service_out(s) for s in rows]


@router.post("/services", status_code=201)
def create_service(body: ServiceIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    s = Service(tenant_id=ctx.tenant_id, **body.model_dump())
    s.name = s.name.strip()
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("A service with this name already exists.")
    return service_out(s)


def _service(db: Session, ctx: TenantContext, sid: str) -> Service:
    s = db.get(Service, sid)
    if s is None or s.tenant_id != ctx.tenant_id:
        raise NotFound("Service not found.")
    return s


@router.put("/services/{sid}")
def update_service(sid: str, body: ServiceIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    s = _service(db, ctx, sid)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("A service with this name already exists.")
    return service_out(s)


@router.delete("/services/{sid}")
def delete_service(sid: str, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    s = _service(db, ctx, sid)
    if db.scalar(select(Appointment.id).where(Appointment.service_id == s.id).limit(1)):
        s.is_active = False  # keep history intact
        db.commit()
        return {"ok": True, "archived": True}
    db.delete(s)
    db.commit()
    return {"ok": True, "archived": False}


# ------------------------------------------------------------------ staff
@router.get("/staff")
def list_staff(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    rows = db.scalars(select(StaffMember).where(StaffMember.tenant_id == ctx.tenant_id).order_by(StaffMember.name)).all()
    return [staff_out(s) for s in rows]


def _apply_staff(db: Session, ctx: TenantContext, s: StaffMember, body: StaffIn) -> None:
    s.name, s.title, s.email, s.is_active = body.name.strip(), body.title.strip(), body.email.strip(), body.is_active
    valid = set(db.scalars(select(Service.id).where(Service.tenant_id == ctx.tenant_id, Service.id.in_(body.service_ids))).all())
    if len(valid) != len(set(body.service_ids)):
        raise AppError("One or more services do not exist.", code="invalid_service")
    for a in body.availability:
        if parse_hhmm(a.start) >= parse_hhmm(a.end):
            raise AppError("Staff availability: end time must be after start time.", code="invalid_hours")
    s.service_links.clear()
    s.availability.clear()
    db.flush()
    for sid in valid:
        s.service_links.append(StaffService(service_id=sid, tenant_id=ctx.tenant_id))
    for a in body.availability:
        s.availability.append(StaffAvailability(tenant_id=ctx.tenant_id, weekday=a.weekday,
                                                start_time=parse_hhmm(a.start), end_time=parse_hhmm(a.end)))


@router.post("/staff", status_code=201)
def create_staff(body: StaffIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    s = StaffMember(tenant_id=ctx.tenant_id, name=body.name)
    db.add(s)
    db.flush()
    _apply_staff(db, ctx, s, body)
    db.commit()
    db.refresh(s)
    return staff_out(s)


@router.put("/staff/{staff_id}")
def update_staff(staff_id: str, body: StaffIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    s = db.get(StaffMember, staff_id)
    if s is None or s.tenant_id != ctx.tenant_id:
        raise NotFound("Staff member not found.")
    _apply_staff(db, ctx, s, body)
    db.commit()
    db.refresh(s)
    return staff_out(s)


@router.delete("/staff/{staff_id}")
def delete_staff(staff_id: str, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    s = db.get(StaffMember, staff_id)
    if s is None or s.tenant_id != ctx.tenant_id:
        raise NotFound("Staff member not found.")
    s.is_active = False
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ knowledge
def kb_out(k: KnowledgeEntry) -> dict:
    return {"id": k.id, "kind": k.kind, "question": k.question, "answer": k.answer, "is_active": k.is_active}


@router.get("/knowledge")
def list_kb(ctx: TenantContext = Depends(require_tenant), db: Session = Depends(get_db)):
    rows = db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.tenant_id == ctx.tenant_id).order_by(KnowledgeEntry.created_at)).all()
    return [kb_out(k) for k in rows]


@router.post("/knowledge", status_code=201)
def create_kb(body: KnowledgeIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    k = KnowledgeEntry(tenant_id=ctx.tenant_id, **body.model_dump())
    db.add(k)
    db.commit()
    return kb_out(k)


@router.put("/knowledge/{kid}")
def update_kb(kid: str, body: KnowledgeIn, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    k = db.get(KnowledgeEntry, kid)
    if k is None or k.tenant_id != ctx.tenant_id:
        raise NotFound("Entry not found.")
    for f, v in body.model_dump().items():
        setattr(k, f, v)
    db.commit()
    return kb_out(k)


@router.delete("/knowledge/{kid}")
def delete_kb(kid: str, ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    k = db.get(KnowledgeEntry, kid)
    if k is None or k.tenant_id != ctx.tenant_id:
        raise NotFound("Entry not found.")
    db.delete(k)
    db.commit()
    return {"ok": True}


MAX_IMPORT_BYTES = 200_000


def parse_knowledge_file(filename: str, raw: bytes) -> list[tuple[str, str]]:
    """CSV with question,answer columns, or TXT with 'Q: ... / A: ...' pairs separated by blank lines."""
    if len(raw) > MAX_IMPORT_BYTES:
        raise AppError("File is too large (max 200 KB).", code="file_too_large")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AppError("File must be UTF-8 text.", code="invalid_file")
    pairs: list[tuple[str, str]] = []
    if filename.lower().endswith(".csv"):
        reader = csv.reader(io.StringIO(text))
        for i, row in enumerate(reader):
            if len(row) < 2:
                continue
            q, a = row[0].strip(), row[1].strip()
            if i == 0 and q.lower() in ("question", "q") and a.lower() in ("answer", "a"):
                continue
            if q and a:
                pairs.append((q, a))
    elif filename.lower().endswith(".txt"):
        for block in text.replace("\r\n", "\n").split("\n\n"):
            q = a = ""
            for line in block.strip().split("\n"):
                if line.lower().startswith("q:"):
                    q = line[2:].strip()
                elif line.lower().startswith("a:"):
                    a = line[2:].strip()
                elif a:
                    a += " " + line.strip()
            if q and a:
                pairs.append((q, a))
    else:
        raise AppError("Upload a .csv or .txt file.", code="invalid_file")
    pairs = [(q[:300], a[:2000]) for q, a in pairs][:500]
    if not pairs:
        raise AppError("No question/answer pairs found in the file.", code="empty_import")
    return pairs


@router.post("/knowledge/import")
async def import_kb(file: UploadFile = File(...), ctx: TenantContext = Depends(require_owner), db: Session = Depends(get_db)):
    raw = await file.read(MAX_IMPORT_BYTES + 1)
    pairs = parse_knowledge_file(file.filename or "", raw)
    for q, a in pairs:
        db.add(KnowledgeEntry(tenant_id=ctx.tenant_id, kind="faq", question=q, answer=a))
    db.commit()
    return {"imported": len(pairs)}

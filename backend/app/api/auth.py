from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db, utcnow
from ..deps import CSRF_COOKIE, SESSION_COOKIE, AuthContext, client_ip, load_session, require_auth
from ..errors import AppError, Conflict, Forbidden, Unauthorized
from ..models import AuthSession, Membership, Tenant, User
from ..ratelimit import limiter
from ..security import DUMMY_HASH, hash_password, new_token, token_hash, verify_password
from ..services.audit import audit
from ..services.tenants import BUSINESS_TYPES, create_tenant, first_membership
from ..services.timeutil import is_valid_timezone

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _password_ok(v: str) -> str:
    if len(v) < 10:
        raise ValueError("Password must be at least 10 characters.")
    if v.lower() == v or not any(ch.isdigit() for ch in v):
        raise ValueError("Use upper- and lower-case letters and at least one number.")
    return v


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)
    full_name: str = Field(min_length=1, max_length=120)
    business_name: str = Field(min_length=2, max_length=160)
    business_type: str = "other"
    timezone: str = "UTC"

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _password_ok(v)

    @field_validator("business_type")
    @classmethod
    def _bt(cls, v: str) -> str:
        if v not in BUSINESS_TYPES:
            raise ValueError("Unknown business type.")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        if not is_valid_timezone(v):
            raise ValueError("Unknown time zone.")
        return v


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)  # not format-validated: unknown e-mails simply fail
    password: str = Field(max_length=200)


class SwitchIn(BaseModel):
    tenant_id: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _password_ok(v)


def _start_session(db: Session, response: Response, request: Request, user: User, tenant_id: str | None) -> AuthSession:
    s = get_settings()
    token = new_token()
    sess = AuthSession(
        token_hash=token_hash(token), user_id=user.id, active_tenant_id=tenant_id, csrf_token=new_token(),
        expires_at=utcnow() + timedelta(days=s.session_days),
        user_agent=(request.headers.get("user-agent") or "")[:200],
    )
    db.add(sess)
    user.last_login_at = utcnow()
    db.commit()
    max_age = s.session_days * 86400
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=s.cookie_secure, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, sess.csrf_token, max_age=max_age, httponly=False, secure=s.cookie_secure, samesite="lax", path="/")
    return sess


def me_payload(db: Session, user: User, sess: AuthSession) -> dict:
    rows = db.execute(
        select(Membership, Tenant).join(Tenant, Tenant.id == Membership.tenant_id).where(Membership.user_id == user.id)
    ).all()
    memberships = [{"tenant_id": t.id, "tenant_name": t.name, "role": m.role} for m, t in rows]
    active = next(((m, t) for m, t in rows if t.id == sess.active_tenant_id), None)
    return {
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name, "is_platform_admin": user.is_platform_admin},
        "memberships": memberships,
        "tenant": None if active is None else {
            "id": active[1].id, "name": active[1].name, "business_type": active[1].business_type,
            "timezone": active[1].timezone, "onboarding_completed": active[1].onboarding_completed,
            "onboarding_step": active[1].onboarding_step, "is_demo": active[1].is_demo,
        },
        "role": active[0].role if active else None,
        "csrf_token": sess.csrf_token,
    }


@router.post("/signup", status_code=201)
def signup(body: SignupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    limiter.check(f"signup:{client_ip(request)}", 10, 3600)
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise Conflict("An account with this e-mail already exists.", code="email_taken")
    user = User(email=email, password_hash=hash_password(body.password), full_name=body.full_name.strip())
    db.add(user)
    db.flush()
    tenant = create_tenant(db, name=body.business_name, business_type=body.business_type, timezone=body.timezone,
                           owner_user_id=user.id)
    audit(db, "user.signup", tenant_id=tenant.id, user_id=user.id)
    db.commit()
    sess = _start_session(db, response, request, user, tenant.id)
    return me_payload(db, user, sess)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.lower()
    limiter.check(f"login-ip:{client_ip(request)}", 20, 300)
    limiter.check(f"login-email:{email}", 8, 300)
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        verify_password(DUMMY_HASH, body.password)
        raise Unauthorized("Incorrect e-mail or password.", code="invalid_credentials")
    if not verify_password(user.password_hash, body.password) or not user.is_active:
        audit(db, "user.login_failed", user_id=user.id)
        db.commit()
        raise Unauthorized("Incorrect e-mail or password.", code="invalid_credentials")
    m = first_membership(db, user.id)
    sess = _start_session(db, response, request, user, m.tenant_id if m else None)
    audit(db, "user.login", user_id=user.id, tenant_id=m.tenant_id if m else None)
    db.commit()
    return me_payload(db, user, sess)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    sess = load_session(db, request.cookies.get(SESSION_COOKIE))
    if sess is not None:
        db.delete(sess)
        db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(ctx: AuthContext = Depends(require_auth), db: Session = Depends(get_db)):
    return me_payload(db, ctx.user, ctx.session)


@router.post("/switch-tenant")
def switch_tenant(body: SwitchIn, ctx: AuthContext = Depends(require_auth), db: Session = Depends(get_db)):
    m = db.scalar(select(Membership).where(Membership.user_id == ctx.user.id, Membership.tenant_id == body.tenant_id))
    if m is None and not ctx.user.is_platform_admin:
        raise Forbidden("You do not have access to this business.", code="no_membership")
    if m is None:
        raise Forbidden("Platform admins must be added as a member to open a business dashboard.", code="no_membership")
    ctx.session.active_tenant_id = body.tenant_id
    db.commit()
    return me_payload(db, ctx.user, ctx.session)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, ctx: AuthContext = Depends(require_auth), db: Session = Depends(get_db)):
    limiter.check(f"pw:{ctx.user.id}", 5, 300)
    if not verify_password(ctx.user.password_hash, body.current_password):
        raise AppError("Current password is incorrect.", code="invalid_password")
    ctx.user.password_hash = hash_password(body.new_password)
    # Sign out every other session.
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == ctx.user.id, AuthSession.id != ctx.session.id)):
        db.delete(s)
    audit(db, "user.password_changed", user_id=ctx.user.id, tenant_id=ctx.session.active_tenant_id)
    db.commit()
    return {"ok": True}


@router.get("/sessions")
def list_sessions(ctx: AuthContext = Depends(require_auth), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuthSession).where(AuthSession.user_id == ctx.user.id).order_by(AuthSession.last_seen_at.desc())).all()
    return [{"id": s.id, "user_agent": s.user_agent, "created_at": s.created_at.isoformat(),
             "last_seen_at": s.last_seen_at.isoformat(), "current": s.id == ctx.session.id} for s in rows]


@router.post("/sessions/revoke-others")
def revoke_others(ctx: AuthContext = Depends(require_auth), db: Session = Depends(get_db)):
    n = 0
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == ctx.user.id, AuthSession.id != ctx.session.id)):
        db.delete(s)
        n += 1
    db.commit()
    return {"revoked": n}

"""Request authentication, CSRF and tenant authorisation dependencies.

The active tenant is stored on the server-side session and every request re-checks
that the user still holds a membership in it. Tenant ids sent by the browser are never trusted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db, utcnow
from .errors import Forbidden, Unauthorized
from .models import AuthSession, Membership, Tenant, User
from .ratelimit import limiter
from .security import constant_eq, token_hash

SESSION_COOKIE = "vr_session"
CSRF_COOKIE = "vr_csrf"
CSRF_HEADER = "x-csrf-token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class AuthContext:
    user: User
    session: AuthSession


@dataclass
class TenantContext:
    user: User
    session: AuthSession
    tenant: Tenant
    role: str

    @property
    def tenant_id(self) -> str:
        return self.tenant.id

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def load_session(db: Session, token: str | None) -> AuthSession | None:
    if not token:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    if sess is None or sess.expires_at <= utcnow():
        return None
    return sess


def require_auth(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise Unauthorized("Please sign in.")
    # The session and its user in one query: with a database far away every query is a long trip.
    row = db.execute(select(AuthSession, User).join(User, User.id == AuthSession.user_id)
                     .where(AuthSession.token_hash == token_hash(token))).first()
    if row is None or row[0].expires_at <= utcnow() or not row[1].is_active:
        raise Unauthorized("Please sign in.")
    sess, user = row
    if request.method in UNSAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not constant_eq(header, sess.csrf_token):
            raise Forbidden("Missing or invalid CSRF token.", code="csrf_failed")
    limiter.check(f"api:{sess.id}", 600, 60)
    now = utcnow()
    if now - sess.last_seen_at > timedelta(minutes=5):
        sess.last_seen_at = now
        db.commit()
    return AuthContext(user=user, session=sess)


def require_tenant(ctx: AuthContext = Depends(require_auth), db: Session = Depends(get_db)) -> TenantContext:
    if not ctx.session.active_tenant_id:
        raise Forbidden("No business selected.", code="no_tenant")
    row = db.execute(
        select(Membership, Tenant).join(Tenant, Tenant.id == Membership.tenant_id).where(
            Membership.user_id == ctx.user.id, Membership.tenant_id == ctx.session.active_tenant_id)
    ).first()
    if row is None:
        raise Forbidden("You do not have access to this business.", code="no_membership")
    m, tenant = row
    return TenantContext(user=ctx.user, session=ctx.session, tenant=tenant, role=m.role)


def require_owner(ctx: TenantContext = Depends(require_tenant)) -> TenantContext:
    if not ctx.is_owner:
        raise Forbidden("Only the business owner can change this setting.", code="owner_required")
    return ctx


def require_platform_admin(ctx: AuthContext = Depends(require_auth)) -> AuthContext:
    if not ctx.user.is_platform_admin:
        raise Forbidden("Platform administrator access required.", code="admin_required")
    return ctx


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

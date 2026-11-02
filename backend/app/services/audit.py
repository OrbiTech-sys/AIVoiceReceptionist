from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditEvent


def audit(db: Session, action: str, *, tenant_id: str | None = None, user_id: str | None = None,
          entity_type: str = "", entity_id: str = "", details: dict[str, Any] | None = None) -> None:
    """Record a sensitive operation. Never put secrets or caller speech into `details`. Caller commits."""
    db.add(AuditEvent(tenant_id=tenant_id, user_id=user_id, action=action, entity_type=entity_type,
                      entity_id=entity_id, details=details))

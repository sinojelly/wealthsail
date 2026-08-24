"""TenantContext: active-tenant resolution and validation for API requests.

The access token carries the active ``tenant_id``. A client may request a
different tenant via the ``X-Tenant-Id`` header; the switch is only honored
inside the RLS handshake — PostgreSQL's ``identity.is_tenant_member()``
rejects any tenant the acting user does not belong to, so a forged header
cannot leak rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.errors import AuthRequiredError, ForbiddenError


@dataclass(frozen=True)
class TenantContext:
    user_id: str
    tenant_id: str
    email: str | None = None
    nickname: str | None = None
    tenant_role: str = "VIEWER"

    @property
    def is_owner_or_admin(self) -> bool:
        return self.tenant_role in ("OWNER", "ADMIN")

    def require_roles(self, *allowed: str) -> None:
        """Raise FORBIDDEN unless the tenant role is in ``allowed``."""
        if self.tenant_role not in allowed:
            raise ForbiddenError(
                f"当前角色 {self.tenant_role} 无权执行此操作",
                field_errors=[],
            )


def resolve_tenant_context(
    *,
    user_id: str,
    tenant_id: str,
    header_tenant_id: str | None = None,
    email: str | None = None,
    nickname: str | None = None,
    tenant_role: str = "VIEWER",
) -> TenantContext:
    """Build the active TenantContext.

    RLS enforces actual membership; here we only fail on malformed input.
    """
    if not user_id or not tenant_id:
        raise AuthRequiredError("缺少用户或租户上下文")
    active_tenant = (header_tenant_id or tenant_id).strip()
    if not active_tenant:
        raise AuthRequiredError("缺少活动租户")
    return TenantContext(
        user_id=user_id,
        tenant_id=active_tenant,
        email=email,
        nickname=nickname,
        tenant_role=tenant_role,
    )

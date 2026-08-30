"""Platform identity governance endpoints.

Platform authority is separate from the active tenant role.  The domain
service invokes a database function that checks ``identity.platform_roles``;
being OWNER or ADMIN of the current tenant never satisfies that check.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.common.database import Pool
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
    now_shanghai,
)
from backend.domain import identity_service
from backend.errors import ok

router = APIRouter(prefix="/platform", tags=["platform-identity"])


class PersonalTenantInvitationRequest(BaseModel):
    email: str = Field(description="将注册为独立租户 OWNER 的邮箱")
    tenantName: str = Field(min_length=2, max_length=64, description="个人租户名称")


@router.post("/personal-tenant-invitations")
async def create_personal_tenant_invitation(
    body: PersonalTenantInvitationRequest,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
    request_id: str = Depends(get_request_id),
) -> dict:
    """Create a one-time invitation; plaintext is returned exactly once."""

    data = await identity_service(pool, settings).create_personal_tenant_invitation(
        actor_user_id=ctx.user_id,
        actor_tenant_id=ctx.tenant_id,
        email=body.email,
        tenant_name=body.tenantName,
    )
    return ok(data, request_id=request_id, as_of=now_shanghai())

"""Validated portfolio asset scopes within the active tenant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import asyncpg

from backend.errors import NotFoundError, ValidationError_

AssetScopeKind = Literal["MINE", "TENANT", "MEMBER"]


@dataclass(frozen=True)
class AssetScope:
    kind: AssetScopeKind
    owner_user_id: str | None = None


async def resolve_asset_scope(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    actor_user_id: str,
    kind: str,
    member_id: str | None = None,
) -> AssetScope:
    """Resolve an external query into one server-trusted account filter."""

    normalized = (kind or "MINE").strip().upper()
    if normalized not in {"MINE", "TENANT", "MEMBER"}:
        raise ValidationError_("资产作用域必须为 MINE、TENANT 或 MEMBER")
    if normalized != "MEMBER" and member_id is not None:
        raise ValidationError_("仅 MEMBER 作用域可以指定 memberId")
    if normalized == "MINE":
        return AssetScope(kind="MINE", owner_user_id=actor_user_id)
    if normalized == "TENANT":
        return AssetScope(kind="TENANT", owner_user_id=None)
    if not member_id:
        raise ValidationError_("MEMBER 作用域必须指定 memberId")

    exists = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
              FROM identity.tenant_members tm
             WHERE tm.tenant_id = $1
               AND tm.user_id = $2
               AND tm.status = 'ACTIVE'
        )
        """,
        tenant_id,
        member_id,
    )
    if not exists:
        raise NotFoundError("指定成员不属于当前租户或已停用")
    return AssetScope(kind="MEMBER", owner_user_id=member_id)

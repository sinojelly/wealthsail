"""Initialize the first administrator of an empty self-hosted installation.

The installer supplies a migration/admin connection and secret inputs. This
module does not load configuration, open connections, or expose an HTTP route.
Existing identities are never repaired or assigned a new password here.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

import asyncpg

from backend.common.auth import hash_password
from scripts.backfill_standard_report_jobs import insert_standard_report_jobs
from scripts.standard_report_jobs import is_complete_canonical_schedule


# All callers use the same transaction-scoped lock before inspecting emptiness.
_BOOTSTRAP_LOCK = 5_788_747_803_221


async def bootstrap_admin(
    conn: asyncpg.Connection,
    *,
    email: str,
    password: str,
    nickname: str,
    tenant_name: str,
) -> dict[str, Any]:
    """Create one platform admin, or return the complete matching installation.

    Invalid input raises ``ValueError`` before accessing the connection. Existing
    users or conflicting/incomplete administrator state raise ``RuntimeError``;
    the operator must use a separate repair or data-migration procedure.
    """

    email = email.strip().lower()
    nickname = nickname.strip()
    tenant_name = tenant_name.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("a valid administrator email is required")
    if len(password) < 12 or len(password.encode("utf-8")) > 72:
        raise ValueError("administrator password requires at least 12 characters and at most 72 UTF-8 bytes")
    if not 1 <= len(nickname) <= 32:
        raise ValueError("administrator nickname must contain 1 to 32 characters")
    if not 2 <= len(tenant_name) <= 64:
        raise ValueError("personal tenant name must contain 2 to 64 characters")

    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _BOOTSTRAP_LOCK)
        counts = await conn.fetchrow(
            """
            SELECT (SELECT count(*) FROM identity.profiles) AS profile_count,
                   (SELECT count(*) FROM identity.tenants) AS tenant_count,
                   (SELECT count(*) FROM identity.platform_roles
                     WHERE role = 'PLATFORM_ADMIN') AS platform_admin_count
            """
        )
        if any(int(counts[key]) for key in ("profile_count", "tenant_count", "platform_admin_count")):
            if int(counts["platform_admin_count"]) != 1:
                raise RuntimeError("existing identities or other administrators conflict with first administrator bootstrap")
            existing = await conn.fetchrow(
                """
                SELECT p.id AS user_id, p.email, p.password_hash,
                       t.id AS tenant_id, t.name AS tenant_name,
                       t.status AS tenant_status,
                       tm.role AS membership_role, tm.status AS membership_status,
                       up.default_tenant_id, up.default_asset_scope,
                       pr.status AS platform_admin_status
                  FROM identity.profiles p
                  LEFT JOIN identity.user_preferences up ON up.user_id = p.id
                  LEFT JOIN identity.tenants t ON t.id = up.default_tenant_id
                  LEFT JOIN identity.tenant_members tm
                    ON tm.user_id = p.id AND tm.tenant_id = t.id
                  LEFT JOIN identity.platform_roles pr
                    ON pr.user_id = p.id AND pr.role = 'PLATFORM_ADMIN'
                 WHERE lower(p.email) = $1
                """,
                email,
            )
            if (
                existing is None
                or not existing["password_hash"]
                or existing["tenant_id"] is None
                or existing["tenant_name"] != tenant_name
                or existing["tenant_status"] != "ACTIVE"
                or existing["membership_role"] != "OWNER"
                or existing["membership_status"] != "ACTIVE"
                or existing["default_tenant_id"] != existing["tenant_id"]
                or existing["default_asset_scope"] != "MINE"
                or existing["platform_admin_status"] != "ACTIVE"
            ):
                raise RuntimeError("existing administrator state is incomplete or conflicts with the requested personal tenant")
            jobs = await conn.fetch(
                """
                SELECT job_name, cron_expr, report_session, is_enabled
                  FROM workflow.jobs
                 WHERE tenant_id = $1 AND report_session IN (
                     'PRE_MARKET', 'INTRADAY', 'FUND_1430', 'POST_MARKET'
                 )
                """,
                existing["tenant_id"],
            )
            if not is_complete_canonical_schedule(list(jobs)):
                raise RuntimeError("existing administrator report schedules are incomplete or conflict with bootstrap")
            return {
                "created": False,
                "userId": str(existing["user_id"]),
                "tenantId": str(existing["tenant_id"]),
                "email": email,
            }

        user_id, tenant_id = uuid4(), uuid4()
        await conn.execute(
            """
            INSERT INTO identity.profiles(id, email, nickname, password_hash)
            VALUES ($1, $2, $3, $4)
            """,
            user_id, email, nickname, hash_password(password),
        )
        await conn.execute(
            """
            INSERT INTO identity.tenants(id, name, status, base_currency)
            VALUES ($1, $2, 'ACTIVE', 'CNY')
            """,
            tenant_id, tenant_name,
        )
        await conn.execute(
            """
            INSERT INTO identity.tenant_members(tenant_id, user_id, role, status)
            VALUES ($1, $2, 'OWNER', 'ACTIVE')
            """,
            tenant_id, user_id,
        )
        await conn.execute(
            """
            INSERT INTO identity.user_preferences(
                user_id, default_tenant_id, default_asset_scope
            ) VALUES ($1, $2, 'MINE')
            """,
            user_id, tenant_id,
        )
        await conn.execute(
            """
            INSERT INTO identity.platform_roles(user_id, role, status, granted_by)
            VALUES ($1, 'PLATFORM_ADMIN', 'ACTIVE', $1)
            """,
            user_id,
        )
        await insert_standard_report_jobs(conn, tenant_id)
        await conn.execute(
            """
            INSERT INTO audit.audit_log(
                tenant_id, actor_type, actor_id, action, resource_type,
                resource_id, details
            ) VALUES ($1, 'USER', $2, 'SELF_HOSTED_ADMIN_BOOTSTRAPPED',
                      'identity.profiles', $2, $3)
            """,
            tenant_id,
            str(user_id),
            json.dumps({"platformRole": "PLATFORM_ADMIN", "reportSchedulesInitialized": True}),
        )
        return {
            "created": True,
            "userId": str(user_id),
            "tenantId": str(tenant_id),
            "email": email,
        }


__all__ = ["bootstrap_admin"]

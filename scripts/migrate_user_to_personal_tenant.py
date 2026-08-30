"""Move one existing user into a deterministic personal tenant.

This is a migration-role operation, not an application endpoint.  It changes
only identity membership/preferences, sessions, platform role and audit rows;
portfolio accounts and ledger facts are read before and after but never
written.  The default mode is a read-only preflight.  ``--apply`` requires an
exact database target and email confirmation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import asyncpg

# ``python scripts/migrate_user_to_personal_tenant.py`` puts the scripts
# directory—not the repository root—at sys.path[0].  Use the sibling import for
# that documented operational form, while retaining the package import used by
# pytest and ``python -m``.
if __package__:
    from scripts.migrate import safe_target_name
else:
    from migrate import safe_target_name


@dataclass(frozen=True)
class MigrationRequest:
    email: str
    source_tenant_id: UUID
    personal_tenant_name: str
    grant_platform_admin: bool = False

    def __post_init__(self) -> None:
        normalized_email = self.email.strip().lower()
        normalized_name = self.personal_tenant_name.strip()
        if "@" not in normalized_email:
            raise ValueError("a valid email is required")
        if not 2 <= len(normalized_name) <= 64:
            raise ValueError("personal tenant name must contain 2 to 64 characters")
        object.__setattr__(self, "email", normalized_email)
        object.__setattr__(self, "personal_tenant_name", normalized_name)


def personal_tenant_id(user_id: UUID) -> UUID:
    """Return a stable id so a retried operation cannot create two tenants."""

    return uuid5(NAMESPACE_URL, f"wealthsail:personal-tenant:{user_id}")


async def _load_state(conn: asyncpg.Connection, request: MigrationRequest) -> Any:
    return await conn.fetchrow(
        """
        SELECT p.id AS user_id,
               p.email,
               p.nickname,
               t.name AS source_tenant_name,
               tm.role AS source_role,
               tm.status AS source_membership_status,
               (
                   SELECT COUNT(*)
                     FROM identity.tenant_members owner_member
                    WHERE owner_member.tenant_id = tm.tenant_id
                      AND owner_member.user_id <> p.id
                      AND owner_member.role = 'OWNER'
                      AND owner_member.status = 'ACTIVE'
               ) AS remaining_active_owner_count,
               (
                   SELECT COUNT(*)
                     FROM identity.account_members am
                    WHERE am.tenant_id = tm.tenant_id
                      AND am.user_id = p.id
                      AND am.effective_to IS NULL
                      AND am.relationship IN ('BENEFICIAL_OWNER', 'JOINT_OWNER')
               ) AS explicit_owned_account_count,
               up.default_tenant_id,
               (
                   SELECT pr.status
                     FROM identity.platform_roles pr
                    WHERE pr.user_id = p.id
                      AND pr.role = 'PLATFORM_ADMIN'
               ) AS platform_admin_status
          FROM identity.profiles p
          JOIN identity.tenant_members tm
            ON tm.user_id = p.id AND tm.tenant_id = $2
          JOIN identity.tenants t ON t.id = tm.tenant_id
          LEFT JOIN identity.user_preferences up ON up.user_id = p.id
         WHERE lower(p.email) = lower($1)
         FOR UPDATE OF p, tm
        """,
        request.email,
        request.source_tenant_id,
    )


async def _load_target(
    conn: asyncpg.Connection, *, target_tenant_id: UUID, user_id: UUID
) -> Any:
    return await conn.fetchrow(
        """
        SELECT t.name AS target_tenant_name,
               tm.role AS target_membership_role,
               tm.status AS target_membership_status
          FROM identity.tenants t
          LEFT JOIN identity.tenant_members tm
            ON tm.tenant_id = t.id AND tm.user_id = $2
         WHERE t.id = $1
        """,
        target_tenant_id,
        user_id,
    )


async def _source_fact_counts(conn: asyncpg.Connection, tenant_id: UUID) -> dict[str, int]:
    row = await conn.fetchrow(
        """
        SELECT
            (SELECT COUNT(*) FROM identity.accounts
              WHERE tenant_id = $1) AS account_count,
            (SELECT COUNT(*) FROM ledger.ledger_events
              WHERE tenant_id = $1) AS ledger_event_count,
            (SELECT COUNT(*) FROM ledger.position_snapshots
              WHERE tenant_id = $1) AS position_snapshot_count,
            (SELECT COUNT(*) FROM ledger.account_nav_snapshots
              WHERE tenant_id = $1) AS nav_snapshot_count,
            (SELECT COUNT(*) FROM ledger.portfolio_import_batches
              WHERE tenant_id = $1) AS import_batch_count
        """,
        tenant_id,
    )
    if row is None:  # pragma: no cover - aggregate SELECT always returns one row
        raise RuntimeError("could not read source tenant portfolio fact counts")
    return {
        "accounts": int(row["account_count"]),
        "ledgerEvents": int(row["ledger_event_count"]),
        "positionSnapshots": int(row["position_snapshot_count"]),
        "navSnapshots": int(row["nav_snapshot_count"]),
        "importBatches": int(row["import_batch_count"]),
    }


def _target_is_exact(
    target: Any,
    state: Any,
    request: MigrationRequest,
    target_tenant_id: UUID,
) -> bool:
    if target is None:
        return False
    platform_ok = (
        not request.grant_platform_admin
        or state["platform_admin_status"] == "ACTIVE"
    )
    return (
        target["target_tenant_name"] == request.personal_tenant_name
        and target["target_membership_role"] == "OWNER"
        and target["target_membership_status"] == "ACTIVE"
        and state["source_membership_status"] == "SUSPENDED"
        and state["default_tenant_id"] == target_tenant_id
        and platform_ok
    )


async def migrate_user(
    conn: asyncpg.Connection,
    request: MigrationRequest,
    *,
    apply: bool,
) -> dict[str, Any]:
    """Preflight or atomically apply one exact personal-tenant migration."""

    async with conn.transaction():
        state = await _load_state(conn, request)
        if state is None:
            raise RuntimeError("user/source tenant membership did not match exactly")

        user_id = UUID(str(state["user_id"]))
        target_tenant_id = personal_tenant_id(user_id)
        if target_tenant_id == request.source_tenant_id:
            raise RuntimeError("deterministic target unexpectedly equals source tenant")

        target = await _load_target(
            conn, target_tenant_id=target_tenant_id, user_id=user_id
        )
        if _target_is_exact(target, state, request, target_tenant_id):
            return {
                "status": "ALREADY_APPLIED",
                "userId": str(user_id),
                "sourceTenantId": str(request.source_tenant_id),
                "targetTenantId": str(target_tenant_id),
            }

        if state["source_membership_status"] != "ACTIVE":
            raise RuntimeError("source membership is not ACTIVE and target is incomplete")
        if int(state["remaining_active_owner_count"]) < 1:
            raise RuntimeError("source tenant would have no remaining ACTIVE OWNER")
        if int(state["explicit_owned_account_count"]) != 0:
            raise RuntimeError(
                "user has an explicitly owned account; ownership migration requires review"
            )
        if target is not None:
            raise RuntimeError("deterministic target tenant already exists with different state")

        before_facts = await _source_fact_counts(conn, request.source_tenant_id)
        plan = {
            "status": "READY",
            "userId": str(user_id),
            "email": state["email"],
            "sourceTenantId": str(request.source_tenant_id),
            "sourceTenantName": state["source_tenant_name"],
            "sourceRole": state["source_role"],
            "targetTenantId": str(target_tenant_id),
            "targetTenantName": request.personal_tenant_name,
            "grantPlatformAdmin": request.grant_platform_admin,
            "explicitOwnedAccountCount": int(state["explicit_owned_account_count"]),
            "sourceFactCounts": before_facts,
        }
        if not apply:
            return plan

        await conn.execute(
            """
            INSERT INTO identity.tenants(id, name, status, base_currency)
            VALUES ($1, $2, 'ACTIVE', 'CNY')
            """,
            target_tenant_id,
            request.personal_tenant_name,
        )
        await conn.execute(
            """
            INSERT INTO identity.tenant_members(
                tenant_id, user_id, role, status
            ) VALUES ($1, $2, 'OWNER', 'ACTIVE')
            """,
            target_tenant_id,
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO identity.user_preferences(
                user_id, default_tenant_id, default_asset_scope
            ) VALUES ($1, $2, 'MINE')
            ON CONFLICT (user_id) DO UPDATE
               SET default_tenant_id = EXCLUDED.default_tenant_id,
                   default_asset_scope = 'MINE',
                   updated_at = now()
            """,
            user_id,
            target_tenant_id,
        )
        if request.grant_platform_admin:
            await conn.execute(
                """
                INSERT INTO identity.platform_roles(
                    user_id, role, status, granted_by, granted_at, suspended_at
                ) VALUES ($1, 'PLATFORM_ADMIN', 'ACTIVE', $1, now(), NULL)
                ON CONFLICT (user_id, role) DO UPDATE
                   SET status = 'ACTIVE',
                       granted_by = EXCLUDED.granted_by,
                       granted_at = now(),
                       suspended_at = NULL
                """,
                user_id,
            )
        await conn.execute(
            """
            UPDATE identity.tenant_members
               SET status = 'SUSPENDED'
             WHERE tenant_id = $1 AND user_id = $2 AND status = 'ACTIVE'
            """,
            request.source_tenant_id,
            user_id,
        )
        await conn.execute(
            """
            UPDATE identity.profiles
               SET auth_version = auth_version + 1, updated_at = now()
             WHERE id = $1
            """,
            user_id,
        )
        await conn.execute(
            """
            UPDATE identity.refresh_sessions
               SET revoked_at = COALESCE(revoked_at, now())
             WHERE user_id = $1 AND revoked_at IS NULL
            """,
            user_id,
        )

        await conn.execute(
            """
            INSERT INTO audit.audit_log(
                tenant_id, actor_type, actor_id, action, resource_type,
                resource_id, details
            ) VALUES (
                $1, 'USER', $2, 'TENANT_MEMBERSHIP_SUSPENDED',
                'identity.tenant_members', $2,
                $3::jsonb
            )
            """,
            request.source_tenant_id,
            str(user_id),
            json.dumps(
                {
                    "reason": "MOVED_TO_PERSONAL_TENANT",
                    "targetTenantId": str(target_tenant_id),
                    "sessionsRevoked": True,
                    "authVersionRotated": True,
                }
            ),
        )
        await conn.execute(
            """
            INSERT INTO audit.audit_log(
                tenant_id, actor_type, actor_id, action, resource_type,
                resource_id, details
            ) VALUES (
                $1, 'USER', $2, 'PERSONAL_TENANT_CREATED',
                'identity.tenants', $1::text,
                $3::jsonb
            )
            """,
            target_tenant_id,
            str(user_id),
            json.dumps(
                {
                    "sourceTenantId": str(request.source_tenant_id),
                    "tenantRole": "OWNER",
                    "platformAdminGranted": request.grant_platform_admin,
                }
            ),
        )

        after_facts = await _source_fact_counts(conn, request.source_tenant_id)
        if after_facts != before_facts:
            raise RuntimeError("source tenant portfolio facts changed; transaction aborted")

        plan["status"] = "APPLIED"
        plan["sourceFactsUnchanged"] = True
        return plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--source-tenant-id", required=True, type=UUID)
    parser.add_argument("--personal-tenant-name", required=True)
    parser.add_argument("--grant-platform-admin", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--readback", action="store_true")
    parser.add_argument("--confirm-email")
    parser.add_argument("--confirm-target")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    dsn = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("MIGRATION_DATABASE_URL is required; no default is allowed")
    target = safe_target_name(dsn)
    if args.apply:
        if args.readback:
            raise SystemExit("--apply and --readback are mutually exclusive")
        if (args.confirm_email or "").strip().lower() != args.email.strip().lower():
            raise SystemExit("--confirm-email must exactly match --email")
        if args.confirm_target != target:
            raise SystemExit(f"target confirmation mismatch; expected --confirm-target {target}")

    request = MigrationRequest(
        email=args.email,
        source_tenant_id=args.source_tenant_id,
        personal_tenant_name=args.personal_tenant_name,
        grant_platform_admin=args.grant_platform_admin,
    )
    conn = await asyncpg.connect(dsn)
    try:
        current_user = await conn.fetchval("SELECT current_user")
        if current_user in {
            "wealthsail_api",
            "wealthsail_api_login",
            "wealthsail_worker",
            "wealthsail_worker_login",
        }:
            raise SystemExit("a migration/admin database role is required")
        migration_ready = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM public.wealthsail_schema_migrations
                 WHERE version = 32
                   AND filename = '032_tenant_user_account_ownership.sql'
            )
            """
        )
        if not migration_ready:
            raise SystemExit("migration 032 must be applied before moving the user")
        return await migrate_user(conn, request, apply=args.apply)
    finally:
        await conn.close()


def main() -> None:
    args = _parser().parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

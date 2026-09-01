"""Forward-only PostgreSQL migration runner with checksum and target guard.

Usage:
  MIGRATION_DATABASE_URL=... python scripts/migrate.py \
      --environment production --confirm-target db.example:5432/wealthsail

Development seeds are intentionally outside ``migrations/`` and can never be
selected by this command.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATTERN = re.compile(r"^(\d{3})_[a-zA-Z0-9_.-]+\.sql$")
LOCK_KEY = 853_271_904


@dataclass(frozen=True)
class Migration:
    version: int
    path: Path
    checksum: str
    sql: str


def discover_migrations(directory: Path) -> list[Path]:
    paths = [
        path
        for path in directory.iterdir()
        if path.is_file() and MIGRATION_PATTERN.fullmatch(path.name)
    ]
    return sorted(paths, key=lambda path: (int(path.name[:3]), path.name))


def safe_target_name(dsn: str) -> str:
    parsed = urlsplit(dsn)
    if not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("MIGRATION_DATABASE_URL must include host and database")
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return f"{host}/{parsed.path.strip('/')}"


def is_loopback_database(dsn: str) -> bool:
    """Return whether the DSN hostname is an explicit loopback target.

    This check is based on the parsed hostname rather than the redacted display
    string so adding a port cannot bypass the production migration guard.
    """

    hostname = (urlsplit(dsn).hostname or "").lower()
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def load_migrations(directory: Path) -> list[Migration]:
    result: list[Migration] = []
    seen: set[int] = set()
    for path in discover_migrations(directory):
        version = int(path.name[:3])
        if version in seen:
            raise RuntimeError(f"duplicate migration version: {version:03d}")
        seen.add(version)
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        result.append(Migration(version, path, checksum, sql))
    if not result:
        raise RuntimeError("no migrations found")
    return result


async def _ensure_ledger(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.wealthsail_schema_migrations (
            version INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            checksum_sha256 CHAR(64) NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def _watchlist_policy_has_required_ownership(
    expression: str | None, roles: tuple[str, ...]
) -> bool:
    """Accept only the normalized ownership equality for a watchlist policy."""

    normalized = re.sub(r"\s+", "", (expression or "").lower())
    normalized = normalized.replace("::text", "").replace("(", "").replace(")", "")
    if roles == ("authenticated",):
        return normalized == "user_id=auth.uid"
    if roles == ("wealthsail_api",):
        return (
            normalized
            == "user_id=nullifcurrent_setting'app.user_id',true,''::uuid"
        )
    return False


async def _readback(conn: asyncpg.Connection) -> None:
    roles = await conn.fetch(
        """
        SELECT rolname, rolcanlogin, rolinherit, rolbypassrls
          FROM pg_roles
         WHERE rolname IN ('wealthsail_api','wealthsail_worker')
        """
    )
    role_map = {row["rolname"]: row for row in roles}
    if set(role_map) != {"wealthsail_api", "wealthsail_worker"} or any(
        row["rolcanlogin"] for row in roles
    ):
        raise RuntimeError(
            "runtime group roles are missing or unexpectedly LOGIN-enabled"
        )
    worker_role = role_map["wealthsail_worker"]
    if worker_role["rolinherit"] or not worker_role["rolbypassrls"]:
        raise RuntimeError("worker role must be NOINHERIT BYPASSRLS")
    if not await conn.fetchval(
        "SELECT pg_has_role('wealthsail_worker','wealthsail_api','MEMBER')"
    ):
        raise RuntimeError("worker cannot explicitly SET ROLE wealthsail_api")

    pre_market_tables = (
        "market.corporate_actions",
        "reference.instrument_cutoff_rules",
    )
    pre_market_reference_boundary: dict[str, bool] = {}
    for table in pre_market_tables:
        safe_name = table.replace(".", "_")
        for role in ("wealthsail_api", "wealthsail_worker", "authenticated"):
            pre_market_reference_boundary[f"{role}_{safe_name}_read"] = bool(
                await conn.fetchval(
                    "SELECT has_table_privilege($1, $2, $3)",
                    role,
                    table,
                    "SELECT",
                )
            )
        for role in ("wealthsail_api", "wealthsail_worker"):
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                pre_market_reference_boundary[
                    f"{role}_{safe_name}_{privilege.lower()}"
                ] = bool(
                    await conn.fetchval(
                        "SELECT has_table_privilege($1, $2, $3)",
                        role,
                        table,
                        privilege,
                    )
                )
    public_pre_market_read = bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                  FROM pg_class relation
                  JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
                  CROSS JOIN LATERAL aclexplode(
                      COALESCE(relation.relacl, acldefault('r', relation.relowner))
                  ) privilege
                 WHERE (namespace.nspname, relation.relname) IN (
                           ('market', 'corporate_actions'),
                           ('reference', 'instrument_cutoff_rules')
                       )
                   AND privilege.grantee = 0
                   AND privilege.privilege_type = 'SELECT'
            )
            """
        )
    )
    api_reads = all(
        pre_market_reference_boundary[f"wealthsail_api_{table.replace('.', '_')}_read"]
        for table in pre_market_tables
    )
    forbidden_effective_privileges = any(
        allowed
        for name, allowed in pre_market_reference_boundary.items()
        if not name.startswith("wealthsail_api_") or not name.endswith("_read")
    )
    if not api_reads or public_pre_market_read or forbidden_effective_privileges:
        raise RuntimeError("pre-market reference read boundary is missing or unsafe")

    exposed_writes = await conn.fetchval(
        """
        SELECT COUNT(*)
          FROM information_schema.role_table_grants
         WHERE grantee='authenticated'
           AND privilege_type IN ('INSERT','UPDATE','DELETE')
           AND table_schema IN ('reporting','workflow','audit')
        """
    )
    if exposed_writes:
        raise RuntimeError("authenticated still has system-fact write grants")

    demo_users = await conn.fetchval(
        """
        SELECT COUNT(*) FROM identity.profiles
         WHERE lower(email) IN ('zhangming@example.com','lihua@example.com','wangfang@example.com')
           AND password_hash IS NOT NULL
        """
    )
    if demo_users:
        raise RuntimeError("production migration readback found login-ready demo users")

    projection_columns = {
        row["column_name"]
        for row in await conn.fetch(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema='ledger'
               AND table_name='account_nav_snapshots'
               AND column_name IN ('known_value','known_market_value')
            """
        )
    }
    if projection_columns != {"known_value"}:
        raise RuntimeError(
            "account NAV projection uses an incompatible known-value column"
        )

    tiantian_columns = {
        "portfolio_import_items": {
            "tenant_id",
            "batch_id",
            "candidate_holding_started_on",
            "holding_start_status",
            "holding_start_method",
            "holding_start_confidence",
            "holding_start_reasons",
            "user_holding_started_on",
        },
        "portfolio_source_bindings": {
            "tenant_id",
            "account_id",
            "provider_code",
            "external_account_key",
            "status",
            "created_by_batch_id",
        },
        "portfolio_import_source_records": {
            "tenant_id",
            "batch_id",
            "record_type",
            "external_record_id",
            "normalized_payload",
            "content_hash",
        },
        "portfolio_source_transactions": {
            "tenant_id",
            "source_binding_id",
            "source_import_batch_id",
            "external_transaction_id",
            "business_type",
            "source_payload",
        },
        "position_period_metrics": {
            "tenant_id",
            "account_id",
            "instrument_id",
            "source_binding_id",
            "holding_started_on",
            "holding_start_method",
            "holding_start_confidence",
            "period_high_nav",
            "period_low_nav",
            "range_position_pct",
            "calculation_status",
        },
    }
    tiantian_rows = await conn.fetch(
        """
        SELECT table_name, column_name
          FROM information_schema.columns
         WHERE table_schema='ledger'
           AND table_name = ANY($1::text[])
        """,
        list(tiantian_columns),
    )
    actual_columns: dict[str, set[str]] = {
        table_name: set() for table_name in tiantian_columns
    }
    for row in tiantian_rows:
        actual_columns[row["table_name"]].add(row["column_name"])
    missing_columns = {
        table_name: sorted(required - actual_columns[table_name])
        for table_name, required in tiantian_columns.items()
        if required - actual_columns[table_name]
    }
    if missing_columns:
        raise RuntimeError(f"Tiantian import schema columns missing: {missing_columns}")

    terminal_columns = {
        row["column_name"]
        for row in await conn.fetch(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema='ledger'
               AND table_name='portfolio_import_batches'
               AND column_name IN (
                   'terminal_command_type','terminal_idempotency_key',
                   'terminal_content_hash','terminal_reason',
                   'supersedes_batch_id'
               )
            """
        )
    }
    if terminal_columns != {
        "terminal_command_type",
        "terminal_idempotency_key",
        "terminal_content_hash",
        "terminal_reason",
        "supersedes_batch_id",
    }:
        raise RuntimeError("Tiantian terminal command idempotency columns missing")

    draft_governance_constraints = {
        row["conname"]
        for row in await conn.fetch(
            """
            SELECT conname
              FROM pg_constraint
             WHERE conrelid='ledger.portfolio_import_batches'::regclass
               AND conname = ANY($1::text[])
            """,
            [
                "portfolio_import_batches_terminal_reason_check",
                "fk_portfolio_import_batches_supersedes",
            ],
        )
    }
    required_draft_governance_constraints = {
        "portfolio_import_batches_terminal_reason_check",
        "fk_portfolio_import_batches_supersedes",
    }
    if draft_governance_constraints != required_draft_governance_constraints:
        raise RuntimeError("Tiantian import draft-governance constraints missing")

    rls_tables = {
        row["relname"]
        for row in await conn.fetch(
            """
            SELECT c.relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname='ledger'
               AND c.relname = ANY($1::text[])
               AND c.relrowsecurity
               AND c.relforcerowsecurity
            """,
            list(tiantian_columns),
        )
    }
    if rls_tables != set(tiantian_columns):
        raise RuntimeError(
            "Tiantian import tables missing ENABLE/FORCE RLS: "
            + ", ".join(sorted(set(tiantian_columns) - rls_tables))
        )

    contract_definitions = "\n".join(
        row["definition"]
        for row in await conn.fetch(
            """
            SELECT pg_get_constraintdef(c.oid) AS definition
              FROM pg_constraint c
              JOIN pg_class t ON t.oid = c.conrelid
              JOIN pg_namespace n ON n.oid = t.relnamespace
             WHERE n.nspname IN ('ledger','workflow')
               AND t.relname IN (
                   'portfolio_import_batches','portfolio_import_items',
                   'portfolio_source_bindings','portfolio_import_source_records',
                   'portfolio_source_transactions','position_period_metrics','job_runs'
               )
            """
        )
    )
    for token in (
        "TIANTIAN_FUND_ZIP",
        "POSITION_SNAPSHOT",
        "FUND_NAV_BACKFILL",
        "POSITION_PERIOD_METRICS_REBUILD",
        "PORTFOLIO_ACTIVITY_ESTIMATE",
    ):
        if token not in contract_definitions:
            raise RuntimeError(
                f"Tiantian import database constraint missing token: {token}"
            )

    required_triggers = {
        "protect_portfolio_source_bindings_trigger",
        "protect_portfolio_import_source_records_trigger",
        "protect_portfolio_source_transactions_trigger",
        "protect_portfolio_import_batches_trigger",
    }
    actual_triggers = {
        row["tgname"]
        for row in await conn.fetch(
            """
            SELECT tgname
              FROM pg_trigger
             WHERE NOT tgisinternal
               AND tgname = ANY($1::text[])
            """,
            list(required_triggers),
        )
    }
    if actual_triggers != required_triggers:
        raise RuntimeError(
            "Tiantian import integrity triggers missing: "
            + ", ".join(sorted(required_triggers - actual_triggers))
        )

    tiantian_privileges = {
        "api_binding_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'ledger.portfolio_source_bindings','SELECT')"
        ),
        "api_binding_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'ledger.portfolio_source_bindings','INSERT')"
        ),
        "api_source_record_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'ledger.portfolio_import_source_records','INSERT')"
        ),
        "api_source_transaction_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'ledger.portfolio_source_transactions','INSERT')"
        ),
        "api_metrics_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'ledger.position_period_metrics','SELECT')"
        ),
        "api_terminal_command_update": await conn.fetchval(
            """
            SELECT has_column_privilege(
                'wealthsail_api','ledger.portfolio_import_batches',
                'terminal_content_hash','UPDATE'
            )
            """
        ),
        "api_import_terminal_reason_update": await conn.fetchval(
            """
            SELECT has_column_privilege(
                'wealthsail_api','ledger.portfolio_import_batches',
                'terminal_reason','UPDATE'
            )
            """
        ),
        "api_holding_start_decision_update": await conn.fetchval(
            """
            SELECT has_column_privilege(
                'wealthsail_api','ledger.portfolio_import_items',
                'user_holding_started_on','UPDATE'
            )
            """
        ),
        "worker_source_transaction_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.portfolio_source_transactions','SELECT')"
        ),
        "worker_source_binding_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.portfolio_source_bindings','SELECT')"
        ),
        "worker_import_batch_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.portfolio_import_batches','SELECT')"
        ),
        "worker_import_item_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.portfolio_import_items','SELECT')"
        ),
        "worker_metrics_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.position_period_metrics','INSERT')"
        ),
        "worker_metrics_update": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'ledger.position_period_metrics','UPDATE')"
        ),
    }
    missing_tiantian_privileges = [
        name for name, allowed in tiantian_privileges.items() if not allowed
    ]
    if missing_tiantian_privileges:
        raise RuntimeError(
            "Tiantian import runtime privileges missing: "
            + ", ".join(missing_tiantian_privileges)
        )

    # The API only needs these columns to correlate a Tiantian import batch
    # with its latest durable data task.  A table-wide SELECT would expose
    # worker lease, retry and error details that are not part of the API
    # contract, so readback rejects both missing columns and an over-broad
    # table grant.
    job_status_columns = (
        "id",
        "tenant_id",
        "status",
        "job_type",
        "payload",
        "available_at",
    )
    missing_job_status_columns = [
        column
        for column in job_status_columns
        if not await conn.fetchval(
            "SELECT has_column_privilege("
            "'wealthsail_api','workflow.job_runs',$1,'SELECT')",
            column,
        )
    ]
    broad_job_status_select = await conn.fetchval(
        "SELECT has_table_privilege('wealthsail_api','workflow.job_runs','SELECT')"
    )
    if missing_job_status_columns or broad_job_status_select:
        details = ", ".join(missing_job_status_columns) or "table-wide SELECT"
        raise RuntimeError(
            "Tiantian job status column privileges missing or too broad: " + details
        )

    job_status_policy = await conn.fetchrow(
        """
        SELECT qual
          FROM pg_policies
         WHERE schemaname='workflow'
           AND tablename='job_runs'
           AND policyname='job_runs_api_select'
           AND cmd='SELECT'
           AND 'wealthsail_api' = ANY(roles)
        """
    )
    if job_status_policy is None or "identity.is_tenant_member" not in str(
        job_status_policy["qual"] or ""
    ):
        raise RuntimeError("Tiantian job status SELECT policy is missing or too broad")

    import_item_update_policy = await conn.fetchrow(
        """
        SELECT qual, with_check
          FROM pg_policies
         WHERE schemaname='ledger'
           AND tablename='portfolio_import_items'
           AND policyname='domain_api_update'
           AND cmd='UPDATE'
           AND 'wealthsail_api' = ANY(roles)
        """
    )
    required_update_policy_tokens = (
        "identity.has_tenant_role",
        "portfolio_import_batches",
        "status = 'DRAFT'",
    )
    if import_item_update_policy is None or any(
        token not in str(import_item_update_policy[field] or "")
        for field in ("qual", "with_check")
        for token in required_update_policy_tokens
    ):
        raise RuntimeError(
            "portfolio import item UPDATE policy is missing or too broad"
        )

    browser_import_writes = await conn.fetchval(
        """
        SELECT COUNT(*)
          FROM information_schema.role_table_grants
         WHERE grantee='authenticated'
           AND privilege_type IN ('INSERT','UPDATE','DELETE')
           AND table_schema='ledger'
           AND table_name IN (
               'portfolio_source_bindings','portfolio_import_source_records',
               'portfolio_source_transactions','position_period_metrics'
           )
        """
    )
    if browser_import_writes:
        raise RuntimeError("authenticated has direct writes to Tiantian import tables")

    auth_version_column = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema='identity' AND table_name='profiles'
          AND column_name='auth_version' AND is_nullable='NO'
        """
    )
    if auth_version_column != 1:
        raise RuntimeError("identity.profiles.auth_version is missing or nullable")

    password_reset_rls = await conn.fetchrow(
        """
        SELECT c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname='identity' AND c.relname='password_reset_tokens'
        """
    )
    if not password_reset_rls or not all(password_reset_rls.values()):
        raise RuntimeError("password reset tokens are missing ENABLE/FORCE RLS")

    forbidden_l1_functions = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname='identity'
          AND p.proname IN ('register_with_tenant','create_tenant')
        """
    )
    if forbidden_l1_functions:
        raise RuntimeError("L1-prohibited self-registration RPCs are still installed")

    reset_function = await conn.fetchrow(
        """
        SELECT p.prosecdef, pg_get_functiondef(p.oid) AS definition
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname='identity' AND p.proname='reset_password'
          AND pg_get_function_identity_arguments(p.oid)=
              'p_token_hash text, p_new_password_hash text'
        """
    )
    if (
        not reset_function
        or not reset_function["prosecdef"]
        or "auth_version = auth_version + 1" not in reset_function["definition"]
    ):
        raise RuntimeError("reset_password does not rotate the access-token version")

    reset_privileges = {
        "api": await conn.fetchval(
            "SELECT has_function_privilege('wealthsail_api',"
            "'identity.reset_password(text,text)','EXECUTE')"
        ),
        "public": await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.routine_privileges
                WHERE routine_schema='identity'
                  AND routine_name='reset_password'
                  AND grantee='PUBLIC'
                  AND privilege_type='EXECUTE'
            )
            """
        ),
        "authenticated": await conn.fetchval(
            "SELECT has_function_privilege('authenticated',"
            "'identity.reset_password(text,text)','EXECUTE')"
        ),
    }
    if (
        not reset_privileges["api"]
        or reset_privileges["public"]
        or reset_privileges["authenticated"]
    ):
        raise RuntimeError("reset_password EXECUTE privileges are not least-privileged")

    confirmation_constraints = "\n".join(
        row["definition"]
        for row in await conn.fetch(
            """
            SELECT pg_get_constraintdef(c.oid) AS definition
            FROM pg_constraint c
            WHERE c.conrelid='ledger.trade_fills'::regclass
              AND c.conname='fk_trade_fills_confirmed_by_tenant'
            """
        )
    )
    if not all(
        token in confirmation_constraints
        for token in ("tenant_id", "confirmed_by", "tenant_members")
    ):
        raise RuntimeError("trade fill confirmation actor is not tenant-bound")

    unique_event_link = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM pg_index index_entry
        JOIN pg_class relation ON relation.oid = index_entry.indexrelid
        WHERE relation.relname='uq_trade_fills_ledger_event'
          AND index_entry.indisunique AND index_entry.indisvalid
        """
    )
    if unique_event_link != 1:
        raise RuntimeError("trade fill ledger-event uniqueness index is missing")

    confirmation_policy = await conn.fetchval(
        """
        SELECT with_check
        FROM pg_policies
        WHERE schemaname='ledger' AND tablename='trade_fills'
          AND policyname='domain_api_update'
        """
    )
    if (
        not confirmation_policy
        or "confirmed_by = auth.uid()" not in confirmation_policy
    ):
        raise RuntimeError("trade fill confirmation policy does not bind auth.uid()")

    replay_table = await conn.fetchval(
        "SELECT to_regclass('workflow.wecom_callback_claims') IS NOT NULL"
    )
    replay_function = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname='workflow'
          AND p.proname='claim_wecom_callback'
          AND p.prosecdef
        """
    )
    if not replay_table or replay_function != 1:
        raise RuntimeError("WeCom callback replay guard is missing")

    market_hot_table = await conn.fetchval(
        "SELECT to_regclass('market.market_hot_snapshots') IS NOT NULL"
    )
    if not market_hot_table:
        raise RuntimeError("Worker-owned market-hot read model is missing")

    ownership_tables = {
        row["table_name"]: (row["relrowsecurity"], row["relforcerowsecurity"])
        for row in await conn.fetch(
            """
            SELECT c.relname AS table_name,
                   c.relrowsecurity, c.relforcerowsecurity
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'identity'
               AND c.relname = ANY($1::text[])
            """,
            ["platform_roles", "user_preferences", "account_members"],
        )
    }
    if set(ownership_tables) != {
        "platform_roles",
        "user_preferences",
        "account_members",
    } or any(not all(flags) for flags in ownership_tables.values()):
        raise RuntimeError(
            "tenant user account ownership tables or forced RLS are missing"
        )

    account_ownership_scope = await conn.fetchval(
        """
        SELECT COUNT(*)
          FROM information_schema.columns
         WHERE table_schema='identity' AND table_name='accounts'
           AND column_name='ownership_scope' AND is_nullable='NO'
        """
    )
    if account_ownership_scope != 1:
        raise RuntimeError("identity.accounts ownership_scope is missing or nullable")

    invitation_contract = "\n".join(
        row["definition"]
        for row in await conn.fetch(
            """
            SELECT pg_get_constraintdef(c.oid) AS definition
              FROM pg_constraint c
             WHERE c.conrelid='identity.invitations'::regclass
               AND c.conname IN (
                   'invitations_purpose_check',
                   'invitations_role_check',
                   'invitations_purpose_shape_check'
               )
            """
        )
    )
    if not all(
        token in invitation_contract
        for token in ("JOIN_TENANT", "CREATE_PERSONAL_TENANT", "OWNER")
    ):
        raise RuntimeError("personal tenant invitation shape constraints are missing")

    default_tenant_fk = await conn.fetchval(
        """
        SELECT COUNT(*)
          FROM pg_constraint c
         WHERE c.conrelid='identity.user_preferences'::regclass
           AND c.contype='f'
           AND c.confrelid='identity.tenant_members'::regclass
        """
    )
    if default_tenant_fk != 1:
        raise RuntimeError("default tenant preference is not membership-bound")

    report_subscription_columns = (
        "pre_market_report_enabled",
        "intraday_report_enabled",
        "fund_1430_report_enabled",
        "post_market_report_enabled",
    )
    subscription_columns = {
        row["column_name"]: (row["is_nullable"], row["column_default"])
        for row in await conn.fetch(
            """
            SELECT column_name, is_nullable, column_default
              FROM information_schema.columns
             WHERE table_schema='identity'
               AND table_name='user_preferences'
               AND column_name = ANY($1::text[])
            """,
            list(report_subscription_columns),
        )
    }
    subscription_privileges = {
        (role, privilege): bool(
            await conn.fetchval(
                "SELECT has_table_privilege($1, $2, $3)",
                role,
                "identity.user_preferences",
                privilege,
            )
        )
        for role in ("wealthsail_api", "authenticated", "public")
        for privilege in ("SELECT", "INSERT", "UPDATE")
    }
    subscription_shape_valid = set(subscription_columns) == set(
        report_subscription_columns
    ) and all(
        nullable == "NO" and str(default).lower() in {"true", "true::boolean"}
        for nullable, default in subscription_columns.values()
    )
    subscription_privileges_valid = (
        subscription_privileges[("wealthsail_api", "SELECT")]
        and subscription_privileges[("wealthsail_api", "INSERT")]
        and subscription_privileges[("wealthsail_api", "UPDATE")]
        and not any(
            allowed
            for (role, _privilege), allowed in subscription_privileges.items()
            if role != "wealthsail_api"
        )
    )
    if not subscription_shape_valid or not subscription_privileges_valid:
        raise RuntimeError(
            "report subscription preference boundary is missing or unsafe"
        )

    watchlist_columns = {
        row["column_name"]
        for row in await conn.fetch(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema='identity'
               AND table_name='watchlist'
            """
        )
    }
    watchlist_primary_key = await conn.fetchval(
        """
        SELECT array_agg(attribute.attname ORDER BY key_column.ordinality)
          FROM pg_constraint constraint
          JOIN LATERAL unnest(constraint.conkey) WITH ORDINALITY AS key_column(attnum, ordinality)
            ON TRUE
          JOIN pg_attribute attribute
            ON attribute.attrelid = constraint.conrelid
           AND attribute.attnum = key_column.attnum
         WHERE constraint.conrelid='identity.watchlist'::regclass
           AND constraint.contype='p'
        """
    )
    watchlist_rls_enabled = await conn.fetchval(
        """
        SELECT relrowsecurity
          FROM pg_class
         WHERE oid='identity.watchlist'::regclass
        """
    )
    watchlist_policies = {
        row["policyname"]: (row["cmd"], tuple(row["roles"]), row["qual"], row["with_check"])
        for row in await conn.fetch(
            """
            SELECT policyname, cmd, roles, qual, with_check
              FROM pg_policies
             WHERE schemaname='identity'
               AND tablename='watchlist'
            """
        )
    }
    required_watchlist_policies = {
        "watchlist_self_select": ("SELECT", ("authenticated",), "qual"),
        "watchlist_self_insert": ("INSERT", ("authenticated",), "with_check"),
        "watchlist_self_delete": ("DELETE", ("authenticated",), "qual"),
        "watchlist_domain_api_select": ("SELECT", ("wealthsail_api",), "qual"),
        "watchlist_domain_api_insert": ("INSERT", ("wealthsail_api",), "with_check"),
        "watchlist_domain_api_delete": ("DELETE", ("wealthsail_api",), "qual"),
    }
    watchlist_policies_valid = set(watchlist_policies) == set(
        required_watchlist_policies
    )
    for name, (command, roles, expression_field) in required_watchlist_policies.items():
        policy = watchlist_policies.get(name)
        if policy is None:
            continue
        actual_command, actual_roles, qual, with_check = policy
        expression = qual if expression_field == "qual" else with_check
        watchlist_policies_valid = watchlist_policies_valid and (
            actual_command == command
            and actual_roles == roles
            and _watchlist_policy_has_required_ownership(
                str(expression or ""), actual_roles
            )
        )
    watchlist_privileges = {
        (role, privilege): bool(
            await conn.fetchval(
                "SELECT has_table_privilege($1, $2, $3)",
                role,
                "identity.watchlist",
                privilege,
            )
        )
        for role in ("wealthsail_api", "authenticated", "public")
        for privilege in ("SELECT", "INSERT", "DELETE", "UPDATE")
    }
    watchlist_privileges_valid = (
        all(
            watchlist_privileges[("wealthsail_api", privilege)]
            for privilege in ("SELECT", "INSERT", "DELETE")
        )
        and not watchlist_privileges[("wealthsail_api", "UPDATE")]
        and not any(
            allowed
            for (role, _privilege), allowed in watchlist_privileges.items()
            if role in ("authenticated", "public")
        )
    )
    if (
        watchlist_columns
        != {"user_id", "symbol", "asset_name", "asset_type", "added_at"}
        or watchlist_primary_key != ["user_id", "symbol"]
        or not watchlist_rls_enabled
        or not watchlist_policies_valid
        or not watchlist_privileges_valid
    ):
        raise RuntimeError("watchlist ownership boundary is missing or unsafe")

    browser_ownership_writes = await conn.fetchval(
        """
        SELECT COUNT(*)
          FROM information_schema.role_table_grants
         WHERE grantee='authenticated'
           AND table_schema='identity'
           AND table_name IN (
               'platform_roles', 'user_preferences', 'account_members'
           )
           AND privilege_type IN ('INSERT', 'UPDATE', 'DELETE')
        """
    )
    if browser_ownership_writes:
        raise RuntimeError("identity ownership tables expose browser writes")

    tenant_name_policy = await conn.fetchrow(
        """
        SELECT roles, qual, with_check
          FROM pg_policies
         WHERE schemaname='identity'
           AND tablename='tenants'
           AND policyname='domain_api_tenant_owner_update'
           AND cmd='UPDATE'
        """
    )
    tenant_name_policy_text = (
        " ".join(
            (
                str(tenant_name_policy["qual"] or ""),
                str(tenant_name_policy["with_check"] or ""),
            )
        )
        if tenant_name_policy
        else ""
    )
    if (
        tenant_name_policy is None
        or "wealthsail_api" not in tenant_name_policy["roles"]
        or "has_tenant_role" not in tenant_name_policy_text
        or "OWNER" not in tenant_name_policy_text
    ):
        raise RuntimeError("tenant name update policy is missing or not owner-scoped")

    tenant_name_privileges = {
        "api_name": await conn.fetchval(
            "SELECT has_column_privilege('wealthsail_api','identity.tenants','name','UPDATE')"
        ),
        "api_updated_at": await conn.fetchval(
            "SELECT has_column_privilege('wealthsail_api','identity.tenants','updated_at','UPDATE')"
        ),
        "api_status_denied": not await conn.fetchval(
            "SELECT has_column_privilege('wealthsail_api','identity.tenants','status','UPDATE')"
        ),
        "api_currency_denied": not await conn.fetchval(
            "SELECT has_column_privilege('wealthsail_api','identity.tenants','base_currency','UPDATE')"
        ),
        "browser_name_denied": not await conn.fetchval(
            "SELECT has_column_privilege('authenticated','identity.tenants','name','UPDATE')"
        ),
    }
    invalid_tenant_name_privileges = [
        name for name, allowed in tenant_name_privileges.items() if not allowed
    ]
    if invalid_tenant_name_privileges:
        raise RuntimeError(
            "tenant name column privileges are missing or too broad: "
            + ", ".join(invalid_tenant_name_privileges)
        )

    platform_invitation_function = await conn.fetchrow(
        """
        SELECT p.prosecdef,
               COALESCE(
                   p.proconfig @> ARRAY['search_path=identity, pg_temp'], false
               ) AS fixed_search_path,
               has_function_privilege(
                   'wealthsail_api',
                   'identity.create_personal_tenant_invitation(uuid,text,text,text,timestamptz)',
                   'EXECUTE'
               ) AS api_can_execute,
               has_function_privilege(
                   'authenticated',
                   'identity.create_personal_tenant_invitation(uuid,text,text,text,timestamptz)',
                   'EXECUTE'
               ) AS browser_can_execute,
               has_function_privilege(
                   'wealthsail_api',
                   'identity.is_platform_admin(uuid)',
                   'EXECUTE'
               ) AS api_can_probe_platform_roles
          FROM pg_proc p
         WHERE p.oid =
               'identity.create_personal_tenant_invitation(uuid,text,text,text,timestamptz)'::regprocedure
        """
    )
    if (
        platform_invitation_function is None
        or not platform_invitation_function["prosecdef"]
        or not platform_invitation_function["fixed_search_path"]
        or not platform_invitation_function["api_can_execute"]
        or platform_invitation_function["browser_can_execute"]
        or platform_invitation_function["api_can_probe_platform_roles"]
    ):
        raise RuntimeError(
            "platform personal-tenant invitation privilege boundary is invalid"
        )

    personal_tenant_acceptance = await conn.fetchrow(
        """
        SELECT p.prosecdef,
               pg_get_functiondef(p.oid) AS definition,
               has_table_privilege(
                   pg_get_userbyid(p.proowner), 'workflow.jobs', 'INSERT'
               ) AS function_owner_can_insert,
               has_table_privilege(
                   'wealthsail_api', 'workflow.jobs', 'INSERT'
               ) AS api_can_insert_jobs,
               has_table_privilege(
                   'authenticated', 'workflow.jobs', 'INSERT'
               ) AS browser_can_insert_jobs
          FROM pg_proc p
         WHERE p.oid =
               'identity.accept_invitation(text,text,text,text)'::regprocedure
        """
    )
    required_personal_schedule_tokens = (
        "IF v_inv.purpose = 'CREATE_PERSONAL_TENANT' THEN",
        "INSERT INTO workflow.jobs",
        "'PRE_MARKET'",
        "'INTRADAY'",
        "'FUND_1430'",
        "'POST_MARKET'",
    )
    if personal_tenant_acceptance is None:
        raise RuntimeError(
            "personal tenant report schedule initialization is missing or unsafe: "
            "function_missing=true"
        )
    personal_schedule_definition = str(personal_tenant_acceptance["definition"] or "")
    missing_personal_schedule_tokens = [
        token
        for token in required_personal_schedule_tokens
        if token not in personal_schedule_definition
    ]
    invalid_personal_schedule_contracts = []
    if not personal_tenant_acceptance["prosecdef"]:
        invalid_personal_schedule_contracts.append("security_definer=false")
    if not personal_tenant_acceptance["function_owner_can_insert"]:
        invalid_personal_schedule_contracts.append("function_owner_can_insert=false")
    if personal_tenant_acceptance["api_can_insert_jobs"]:
        invalid_personal_schedule_contracts.append("api_can_insert_jobs=true")
    if personal_tenant_acceptance["browser_can_insert_jobs"]:
        invalid_personal_schedule_contracts.append("browser_can_insert_jobs=true")
    if missing_personal_schedule_tokens:
        invalid_personal_schedule_contracts.append(
            "missing_definition_tokens=" + repr(missing_personal_schedule_tokens)
        )
    if invalid_personal_schedule_contracts:
        raise RuntimeError(
            "personal tenant report schedule initialization is missing or unsafe: "
            + ", ".join(invalid_personal_schedule_contracts)
        )

    report_job_session_constraint = await conn.fetchval(
        """
        SELECT pg_get_constraintdef(c.oid)
          FROM pg_constraint c
         WHERE c.conrelid = 'workflow.jobs'::regclass
           AND c.conname = 'uq_jobs_tenant_report_session'
           AND c.contype = 'u'
           AND c.convalidated
        """
    )
    if report_job_session_constraint != "UNIQUE (tenant_id, report_session)":
        raise RuntimeError(
            "report job session uniqueness constraint is missing or unsafe"
        )

    shared_market_brief_boundary = await conn.fetchrow(
        """
        WITH boundary AS (
            SELECT to_regclass('reporting.shared_market_briefs') AS relation_id
        )
        SELECT relation_id IS NOT NULL AS table_exists,
               CASE WHEN relation_id IS NOT NULL THEN has_table_privilege(
                   'wealthsail_api', relation_id, 'SELECT'
               ) ELSE FALSE END AS api_can_select,
               CASE WHEN relation_id IS NOT NULL THEN has_table_privilege(
                   'wealthsail_api', relation_id, 'INSERT'
               ) ELSE FALSE END AS api_can_insert,
               CASE WHEN relation_id IS NOT NULL THEN has_table_privilege(
                   'wealthsail_worker', relation_id, 'SELECT'
               ) ELSE FALSE END AS worker_can_select,
               CASE WHEN relation_id IS NOT NULL THEN has_table_privilege(
                   'wealthsail_worker', relation_id, 'INSERT'
               ) ELSE FALSE END AS worker_can_insert,
               CASE WHEN relation_id IS NOT NULL THEN NOT has_table_privilege(
                   'wealthsail_api', relation_id, 'UPDATE'
               ) ELSE FALSE END AS api_cannot_update,
               CASE WHEN relation_id IS NOT NULL THEN NOT has_table_privilege(
                   'wealthsail_api', relation_id, 'DELETE'
               ) ELSE FALSE END AS api_cannot_delete,
               CASE WHEN relation_id IS NOT NULL THEN NOT has_table_privilege(
                   'wealthsail_worker', relation_id, 'UPDATE'
               ) ELSE FALSE END AS worker_cannot_update,
               CASE WHEN relation_id IS NOT NULL THEN NOT has_table_privilege(
                   'wealthsail_worker', relation_id, 'DELETE'
               ) ELSE FALSE END AS worker_cannot_delete,
               CASE WHEN relation_id IS NOT NULL THEN NOT has_table_privilege(
                   'authenticated', relation_id, 'SELECT'
               ) ELSE FALSE END AS browser_cannot_select,
               CASE WHEN relation_id IS NOT NULL THEN NOT has_table_privilege(
                   'authenticated', relation_id, 'INSERT'
               ) ELSE FALSE END AS browser_cannot_insert,
               EXISTS (
                   SELECT 1 FROM pg_trigger
                    WHERE tgrelid = relation_id
                      AND tgname = 'protect_shared_market_briefs_trigger'
                      AND NOT tgisinternal
               ) AS append_only_trigger_exists
          FROM boundary
        """
    )
    if shared_market_brief_boundary is None or not all(
        shared_market_brief_boundary.values()
    ):
        raise RuntimeError("shared market brief cache boundary is missing or unsafe")

    data_source_type_contract = await conn.fetchval(
        """
        SELECT pg_get_constraintdef(c.oid)
          FROM pg_constraint c
         WHERE c.conrelid='reference.data_sources'::regclass
           AND c.conname='data_sources_source_type_check'
        """
    )
    fund_nav_source_drift = await conn.fetchval(
        """
        SELECT COUNT(*)
          FROM reference.data_sources
         WHERE name IN ('TiantianFund-EastMoney', 'WealthSail-Fund-Estimator')
           AND source_type <> 'FUND_NAV'
        """
    )
    if (
        not data_source_type_contract
        or "FUND_NAV" not in data_source_type_contract
        or "PUBLIC_API" in data_source_type_contract
        or fund_nav_source_drift
    ):
        raise RuntimeError("fund NAV data source type alignment is missing")

    holding_start_boundary = await conn.fetchrow(
        """
        WITH boundary AS (
            SELECT to_regclass(
                'ledger.position_holding_start_decisions'
            ) AS relation_id
        )
        SELECT relation_id IS NOT NULL AS table_exists,
               COALESCE((
                   SELECT relrowsecurity AND relforcerowsecurity
                     FROM pg_class WHERE oid = relation_id
               ), FALSE) AS forced_rls_exists,
               EXISTS (
                   SELECT 1 FROM pg_trigger
                    WHERE tgrelid = relation_id
                      AND tgname = 'protect_holding_start_decisions_trigger'
                      AND NOT tgisinternal
               ) AS append_only_trigger_exists,
               CASE WHEN relation_id IS NOT NULL THEN has_table_privilege(
                   'wealthsail_api', relation_id, 'INSERT'
               ) ELSE FALSE END AS api_can_insert_holding_start_decision,
               CASE WHEN relation_id IS NOT NULL THEN has_table_privilege(
                   'wealthsail_api', relation_id, 'UPDATE'
               ) ELSE TRUE END AS api_can_update_holding_start_decision,
               CASE WHEN relation_id IS NOT NULL THEN has_table_privilege(
                   'wealthsail_api', relation_id, 'DELETE'
               ) ELSE TRUE END AS api_can_delete_holding_start_decision,
               CASE WHEN relation_id IS NOT NULL THEN has_table_privilege(
                   'authenticated', relation_id, 'SELECT'
               ) ELSE TRUE END AS browser_can_read_holding_start_decision,
               CASE WHEN relation_id IS NOT NULL THEN has_table_privilege(
                   'wealthsail_worker', relation_id, 'SELECT'
               ) ELSE FALSE END AS worker_can_read_holding_start_decision,
               EXISTS (
                   SELECT 1 FROM pg_policies
                    WHERE schemaname = 'ledger'
                      AND tablename = 'position_holding_start_decisions'
                      AND policyname = 'holding_start_decisions_api_select'
                      AND cmd = 'SELECT'
                      AND roles = ARRAY['wealthsail_api']::name[]
               ) AS api_select_policy_exists,
               EXISTS (
                   SELECT 1 FROM pg_policies
                    WHERE schemaname = 'ledger'
                      AND tablename = 'position_holding_start_decisions'
                      AND policyname = 'holding_start_decisions_api_insert'
                      AND cmd = 'INSERT'
                      AND roles = ARRAY['wealthsail_api']::name[]
               ) AS api_insert_policy_exists
          FROM boundary
        """
    )
    if (
        holding_start_boundary is None
        or not holding_start_boundary["table_exists"]
        or not holding_start_boundary["forced_rls_exists"]
        or not holding_start_boundary["append_only_trigger_exists"]
        or not holding_start_boundary["api_can_insert_holding_start_decision"]
        or holding_start_boundary["api_can_update_holding_start_decision"]
        or holding_start_boundary["api_can_delete_holding_start_decision"]
        or holding_start_boundary["browser_can_read_holding_start_decision"]
        or not holding_start_boundary["worker_can_read_holding_start_decision"]
        or not holding_start_boundary["api_select_policy_exists"]
        or not holding_start_boundary["api_insert_policy_exists"]
    ):
        raise RuntimeError(
            "post-import holding-start decision boundary is missing or unsafe"
        )

    worker_projection_boundary = await conn.fetchrow(
        """
        SELECT has_table_privilege(
                   'wealthsail_worker', 'identity.accounts', 'SELECT'
               ) AS worker_can_read_accounts,
               has_table_privilege(
                   'wealthsail_worker', 'ledger.position_snapshots', 'INSERT'
               ) AS worker_can_insert_positions,
               has_table_privilege(
                   'wealthsail_worker', 'ledger.position_snapshots', 'DELETE'
               ) AS worker_can_delete_positions,
               has_column_privilege(
                   'wealthsail_worker', 'ledger.position_snapshots',
                   'market_price', 'UPDATE'
               ) AS worker_can_update_position_prices,
               NOT has_column_privilege(
                   'wealthsail_worker', 'ledger.position_snapshots',
                   'quantity', 'UPDATE'
               ) AS worker_cannot_update_position_facts,
               has_table_privilege(
                   'wealthsail_worker', 'ledger.account_nav_snapshots', 'INSERT'
               ) AS worker_can_insert_account_nav,
               has_column_privilege(
                   'wealthsail_worker', 'ledger.account_nav_snapshots',
                   'known_value', 'UPDATE'
               ) AS worker_can_update_account_nav,
               NOT has_column_privilege(
                   'wealthsail_worker', 'ledger.account_nav_snapshots',
                   'tenant_id', 'UPDATE'
               ) AS worker_cannot_reassign_account_nav,
               EXISTS (
                   SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'ledger'
                      AND table_name = 'position_holding_start_decisions'
                      AND column_name = 'recalculation_required'
                      AND data_type = 'boolean'
                      AND is_nullable = 'NO'
                      AND column_default ILIKE '%true%'
               ) AS confirmation_mode_exists,
               EXISTS (
                   SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'ledger'
                      AND table_name = 'position_holding_start_decisions'
                      AND column_name = 'previous_started_on'
                      AND data_type = 'date'
                      AND is_nullable = 'YES'
               ) AS previous_start_exists
        """
    )
    if (
        worker_projection_boundary is None
        or not worker_projection_boundary["worker_can_read_accounts"]
        or not worker_projection_boundary["worker_can_insert_positions"]
        or not worker_projection_boundary["worker_can_delete_positions"]
        or not worker_projection_boundary["worker_can_update_position_prices"]
        or not worker_projection_boundary["worker_cannot_update_position_facts"]
        or not worker_projection_boundary["worker_can_insert_account_nav"]
        or not worker_projection_boundary["worker_can_update_account_nav"]
        or not worker_projection_boundary["worker_cannot_reassign_account_nav"]
        or not worker_projection_boundary["confirmation_mode_exists"]
        or not worker_projection_boundary["previous_start_exists"]
    ):
        raise RuntimeError(
            "worker valuation projection or holding confirmation boundary is missing"
        )

    performance_baseline_boundary = await conn.fetchrow(
        """
        WITH boundary AS (
            SELECT to_regclass(
                'ledger.account_performance_baselines'
            ) AS relation_id
        )
        SELECT relation_id IS NOT NULL AS table_exists,
               COALESCE((
                   SELECT relrowsecurity AND relforcerowsecurity
                     FROM pg_class WHERE oid = relation_id
               ), FALSE) AS forced_rls_exists,
               EXISTS (
                   SELECT 1 FROM pg_trigger
                    WHERE tgrelid = 'ledger.account_nav_snapshots'::regclass
                      AND tgname =
                          'initialize_account_performance_baseline_trigger'
                      AND NOT tgisinternal
               ) AS initialize_account_performance_baseline_trigger,
               EXISTS (
                   SELECT 1 FROM pg_trigger
                    WHERE tgrelid = relation_id
                      AND tgname =
                          'protect_account_performance_baselines_trigger'
                      AND NOT tgisinternal
               ) AS append_only_trigger_exists,
               CASE WHEN relation_id IS NOT NULL THEN has_table_privilege(
                   'wealthsail_api', relation_id, 'SELECT'
               ) ELSE FALSE END AS api_can_read_performance_baselines,
               CASE WHEN relation_id IS NOT NULL THEN has_table_privilege(
                   'wealthsail_api', relation_id, 'INSERT'
               ) ELSE FALSE END AS api_can_insert_performance_baselines,
               CASE WHEN relation_id IS NOT NULL THEN NOT has_table_privilege(
                   'wealthsail_api', relation_id, 'UPDATE'
               ) ELSE FALSE END AS api_cannot_update_performance_baselines,
               CASE WHEN relation_id IS NOT NULL THEN NOT has_table_privilege(
                   'authenticated', relation_id, 'SELECT'
               ) ELSE FALSE END AS browser_cannot_read_performance_baselines,
               CASE WHEN relation_id IS NOT NULL THEN NOT has_table_privilege(
                   'wealthsail_worker', relation_id, 'INSERT'
               ) ELSE FALSE END AS worker_cannot_insert_performance_baselines,
               EXISTS (
                   SELECT 1 FROM pg_policies
                    WHERE schemaname = 'ledger'
                      AND tablename = 'account_performance_baselines'
                      AND policyname = 'performance_baselines_api_select'
                      AND cmd = 'SELECT'
                      AND roles = ARRAY['wealthsail_api']::name[]
               ) AS api_select_policy_exists,
               EXISTS (
                   SELECT 1 FROM pg_policies
                    WHERE schemaname = 'ledger'
                      AND tablename = 'account_performance_baselines'
                      AND policyname = 'performance_baselines_api_insert'
                      AND cmd = 'INSERT'
                      AND roles = ARRAY['wealthsail_api']::name[]
               ) AS api_insert_policy_exists
          FROM boundary
        """
    )
    if (
        performance_baseline_boundary is None
        or not performance_baseline_boundary["table_exists"]
        or not performance_baseline_boundary["forced_rls_exists"]
        or not performance_baseline_boundary[
            "initialize_account_performance_baseline_trigger"
        ]
        or not performance_baseline_boundary["append_only_trigger_exists"]
        or not performance_baseline_boundary["api_can_read_performance_baselines"]
        or not performance_baseline_boundary["api_can_insert_performance_baselines"]
        or not performance_baseline_boundary["api_cannot_update_performance_baselines"]
        or not performance_baseline_boundary[
            "browser_cannot_read_performance_baselines"
        ]
        or not performance_baseline_boundary[
            "worker_cannot_insert_performance_baselines"
        ]
        or not performance_baseline_boundary["api_select_policy_exists"]
        or not performance_baseline_boundary["api_insert_policy_exists"]
    ):
        raise RuntimeError("account performance baseline boundary is missing or unsafe")

    privilege_checks = {
        "worker_snapshot_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker','market.source_snapshots','INSERT')"
        ),
        "worker_observation_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker','market.market_observations','INSERT')"
        ),
        "api_projection_delete": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api','ledger.position_snapshots','DELETE')"
        ),
        "api_known_value_update": await conn.fetchval(
            """
            SELECT has_column_privilege(
                'wealthsail_api','ledger.account_nav_snapshots','known_value','UPDATE'
            )
            """
        ),
        "api_market_hot_select": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_api',"
            "'market.market_hot_snapshots','SELECT')"
        ),
        "worker_market_hot_insert": await conn.fetchval(
            "SELECT has_table_privilege('wealthsail_worker',"
            "'market.market_hot_snapshots','INSERT')"
        ),
        "browser_market_hot_select": not await conn.fetchval(
            "SELECT has_table_privilege('authenticated',"
            "'market.market_hot_snapshots','SELECT')"
        ),
    }
    missing = [name for name, allowed in privilege_checks.items() if not allowed]
    if missing:
        raise RuntimeError(f"runtime role privileges missing: {', '.join(missing)}")

    seed_versions = await conn.fetchval(
        "SELECT COUNT(*) FROM public.wealthsail_schema_migrations WHERE version IN (6,10)"
    )
    if seed_versions:
        raise RuntimeError("development seed entered the production migration ledger")


async def migrate(dsn: str, migrations: list[Migration], *, dry_run: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        await _ensure_ledger(conn)
        applied = {
            row["version"]: row
            for row in await conn.fetch(
                "SELECT version, filename, checksum_sha256 FROM public.wealthsail_schema_migrations"
            )
        }
        for migration in migrations:
            previous = applied.get(migration.version)
            if previous:
                if previous["checksum_sha256"].strip() != migration.checksum:
                    raise RuntimeError(
                        f"checksum drift at migration {migration.version:03d}: {migration.path.name}"
                    )
                print(f"SKIP {migration.path.name} (already applied)")
                continue
            if dry_run:
                print(f"PLAN {migration.path.name} sha256={migration.checksum[:12]}")
                continue
            async with conn.transaction():
                await conn.execute(migration.sql)
                await conn.execute(
                    """
                    INSERT INTO public.wealthsail_schema_migrations
                        (version, filename, checksum_sha256)
                    VALUES ($1,$2,$3)
                    """,
                    migration.version,
                    migration.path.name,
                    migration.checksum,
                )
            print(f"APPLIED {migration.path.name}")
        if not dry_run:
            await _readback(conn)
            print(
                "READBACK OK: roles, RLS, runtime grants, Tiantian import, "
                "identity session, tenant governance and trade-integrity contracts"
            )
    finally:
        try:
            await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        finally:
            await conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--environment", required=True, choices=("development", "staging", "production")
    )
    parser.add_argument(
        "--confirm-target",
        required=True,
        help="Exact host[:port]/database printed by this command",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dsn = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("MIGRATION_DATABASE_URL is required; no default is allowed")
    target = safe_target_name(dsn)
    if args.confirm_target != target:
        raise SystemExit(
            f"target confirmation mismatch; expected --confirm-target {target}"
        )
    if args.environment == "production" and is_loopback_database(dsn):
        raise SystemExit("production environment cannot target a loopback database")
    print(
        f"TARGET environment={args.environment} database={target} dry_run={args.dry_run}"
    )
    asyncio.run(
        migrate(dsn, load_migrations(ROOT / "migrations"), dry_run=args.dry_run)
    )


if __name__ == "__main__":
    main()

"""Create the minimum login fixture in an ephemeral loopback PostgreSQL.

This command applies all production migrations, provisions the real runtime
login-role boundary, and creates one empty-tenant smoke user. It deliberately
refuses every non-loopback target so it can never seed staging or production.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.common.auth import hash_password  # noqa: E402
from scripts.migrate import load_migrations, migrate  # noqa: E402

CI_API_PASSWORD = "ci-api-password-32-characters-only"
CI_SMOKE_EMAIL = "ci-smoke@wealthsail.example"
CI_SMOKE_PASSWORD = "SmokeLogin-Only-2026!"
CI_USER_ID = "00000000-0000-0000-0000-00000000c101"
CI_TENANT_ID = "00000000-0000-0000-0000-00000000c102"
CI_ACCOUNT_ID = "00000000-0000-0000-0000-00000000c103"


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_admin_target(dsn: str) -> None:
    """The fixture operation is destructive-by-design and local-CI only."""
    parsed = urlsplit(dsn)
    if not parsed.hostname or not _is_loopback(parsed.hostname):
        raise ValueError("CI smoke fixture database must be an explicit loopback target")
    if parsed.username != "postgres":
        raise ValueError("CI smoke fixture database requires the ephemeral postgres admin")
    if parsed.path.strip("/") != "postgres":
        raise ValueError("CI smoke fixture database must be the ephemeral postgres database")


async def prepare(dsn: str) -> None:
    await migrate(dsn, load_migrations(ROOT / "migrations"), dry_run=False)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute((ROOT / "scripts/provision_supabase_roles.sql").read_text("utf-8"))
        # Constant, disposable CI-only credentials. No external secret is
        # interpolated into SQL and the target guard above forbids remote hosts.
        await conn.execute(
            f"ALTER ROLE wealthsail_api_login WITH PASSWORD '{CI_API_PASSWORD}'"
        )
        password_hash = hash_password(CI_SMOKE_PASSWORD)
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO identity.tenants (id, name, status, base_currency)
                VALUES ($1::uuid, 'CI Smoke Tenant', 'ACTIVE', 'CNY')
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    status = EXCLUDED.status
                """,
                CI_TENANT_ID,
            )
            await conn.execute(
                """
                INSERT INTO identity.profiles (id, email, nickname, password_hash)
                VALUES ($1::uuid, $2, 'CI Smoke User', $3)
                ON CONFLICT (id) DO UPDATE SET
                    email = EXCLUDED.email,
                    nickname = EXCLUDED.nickname,
                    password_hash = EXCLUDED.password_hash
                """,
                CI_USER_ID,
                CI_SMOKE_EMAIL,
                password_hash,
            )
            await conn.execute(
                """
                INSERT INTO identity.tenant_members
                    (tenant_id, user_id, role, status)
                VALUES ($1::uuid, $2::uuid, 'VIEWER', 'ACTIVE')
                ON CONFLICT (tenant_id, user_id) DO UPDATE SET
                    role = EXCLUDED.role,
                    status = EXCLUDED.status
                """,
                CI_TENANT_ID,
                CI_USER_ID,
            )
            # A zero-value dated projection is still a real, complete empty
            # portfolio. It makes dashboard GETs return 200 instead of hiding
            # unexpected 5xx failures behind an intentionally empty fixture.
            await conn.execute(
                """
                INSERT INTO identity.accounts
                    (id, tenant_id, account_type, name, base_currency, status)
                VALUES ($1::uuid, $2::uuid, 'MANUAL', 'CI Empty Portfolio', 'CNY', 'ACTIVE')
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    status = EXCLUDED.status
                """,
                CI_ACCOUNT_ID,
                CI_TENANT_ID,
            )
            await conn.execute(
                """
                INSERT INTO ledger.account_nav_snapshots
                    (tenant_id, account_id, as_of, cash_balance,
                     positions_market_value, total_nav, daily_pnl,
                     cumulative_pnl, data_quality_status, known_value,
                     valuation_coverage_pct, unpriced_position_count)
                VALUES
                    ($1::uuid, $2::uuid, CURRENT_DATE, 0, 0, 0, 0, 0,
                     'FRESH', 0, 100, 0)
                ON CONFLICT (tenant_id, account_id, as_of) DO UPDATE SET
                    cash_balance = EXCLUDED.cash_balance,
                    positions_market_value = EXCLUDED.positions_market_value,
                    total_nav = EXCLUDED.total_nav,
                    daily_pnl = EXCLUDED.daily_pnl,
                    cumulative_pnl = EXCLUDED.cumulative_pnl,
                    data_quality_status = EXCLUDED.data_quality_status,
                    known_value = EXCLUDED.known_value,
                    valuation_coverage_pct = EXCLUDED.valuation_coverage_pct,
                    unpriced_position_count = EXCLUDED.unpriced_position_count
                """,
                CI_TENANT_ID,
                CI_ACCOUNT_ID,
            )
    finally:
        await conn.close()


def main() -> None:
    dsn = os.environ.get("CI_SMOKE_ADMIN_DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("CI_SMOKE_ADMIN_DATABASE_URL is required")
    validate_admin_target(dsn)
    asyncio.run(prepare(dsn))
    print("CI smoke database ready: migrations current, runtime roles provisioned, fixture seeded")


if __name__ == "__main__":
    main()

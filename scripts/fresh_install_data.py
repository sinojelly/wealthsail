"""Remove historical approximation data only during a confirmed fresh install.

The installer must authorize this operation from its persistent fresh-install
marker before calling it. An empty identity alone does not establish that an
existing database is a fresh installation. This is deliberately not a general
cleanup command for databases that have ever held application users or assets.
"""

from __future__ import annotations

from datetime import date

import asyncpg


_APPROXIMATION_WARNING = (
    "基于公开指数机制的估值近似，真季报披露后请用 "
    "scripts/ingest_fund_holdings.py 覆盖"
)


async def clean_fresh_install_data(conn: asyncpg.Connection) -> dict:
    """Atomically remove only migration 053/054 approximations from an unused DB.

    Public instrument identities and all schema objects remain intact. Refuse
    the entire operation if personal data or another lookthrough source exists.
    """

    async with conn.transaction():
        # Block concurrent writes while validating the unused database and its
        # complete lookthrough contents; the marker transition may be committed
        # by a caller's surrounding transaction together with this cleanup.
        await conn.execute(
            """
            LOCK TABLE identity.profiles, identity.tenants, identity.accounts,
                ledger.ledger_events, ledger.position_snapshots,
                ledger.account_nav_snapshots, market.fund_lookthrough
            IN SHARE ROW EXCLUSIVE MODE
            """
        )
        counts = await conn.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM identity.profiles) AS profile_count,
                (SELECT count(*) FROM identity.tenants) AS tenant_count,
                (SELECT count(*) FROM identity.accounts) AS account_count,
                (SELECT count(*) FROM ledger.ledger_events) AS event_count,
                (SELECT count(*) FROM ledger.position_snapshots) AS position_count,
                (SELECT count(*) FROM ledger.account_nav_snapshots) AS nav_count
            """
        )
        if counts is None or any(int(value) != 0 for value in counts.values()):
            raise RuntimeError(
                "fresh-install cleanup requires an unused database with empty "
                "profiles, tenants, accounts, events, positions and NAV"
            )

        holdings = await conn.fetch(
            """
            SELECT report_period, source_id,
                   metadata->>'source' AS source,
                   metadata->>'warning' AS warning
              FROM market.fund_lookthrough
            """
        )
        if any(
            row["report_period"] != date(2026, 6, 30)
            or row["source_id"] is not None
            or row["source"] != "index-disclosure"
            or row["warning"] != _APPROXIMATION_WARNING
            for row in holdings
        ):
            raise RuntimeError(
                "fresh-install cleanup found an unexpected lookthrough source; "
                "no holdings were removed"
            )

        removed = await conn.fetchval(
            """
            WITH removed AS (
                DELETE FROM market.fund_lookthrough RETURNING id
            )
            SELECT count(*) FROM removed
            """
        )
        return {"removedApproximateHoldings": int(removed)}

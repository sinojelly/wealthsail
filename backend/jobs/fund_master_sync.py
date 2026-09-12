"""Weekly market-wide fund master refresh for name-based search.

One AkShare call returns every open-ended fund; the Worker upserts them all in
a single transaction through the migration-governed function.  The master list
also doubles as the identity resolver for on-demand NAV fetches, replacing one
provider round-trip per fund.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from asyncpg import Connection

from backend.common.database import Pool, system_connection
from backend.integrations.fund_master import (
    FundMasterEntry,
    fetch_fund_master_list,
)

logger = logging.getLogger("wealthsail.fund_master_sync")


@dataclass(frozen=True, slots=True)
class FundMasterSyncStats:
    total_entries: int
    upserted: int


async def upsert_fund_master_entries(
    conn: Connection, entries: list[FundMasterEntry]
) -> int:
    """Upsert every entry in one transaction; returns the upsert count."""

    async with conn.transaction():
        await conn.executemany(
            """
            SELECT reference.upsert_fund_master($1, $2, $3, $4, $5)
            """,
            [
                (
                    entry.fund_code,
                    entry.fund_name,
                    entry.fund_type,
                    entry.pinyin_abbr,
                    entry.pinyin_full,
                )
                for entry in entries
            ],
        )
    return len(entries)


async def sync_fund_master(pool: Pool) -> FundMasterSyncStats:
    """Refresh the whole master list in one transaction."""

    entries = await fetch_fund_master_list()
    async with system_connection(pool) as conn:
        upserted = await upsert_fund_master_entries(conn, entries)
    logger.info("fund_master_sync_complete total=%d", upserted)
    return FundMasterSyncStats(total_entries=len(entries), upserted=upserted)


async def lookup_fund_master_identity(
    conn: Connection, fund_code: str
) -> tuple[str, str] | None:
    """Resolve a fund identity from the master list, or ``None`` on a miss.

    Used as the fast path for on-demand NAV fetches: the master table already
    knows the canonical name, so the per-fund identity provider call can be
    skipped entirely.
    """

    row = await conn.fetchrow(
        """
        SELECT fund_name, fund_type
          FROM reference.fund_master
         WHERE fund_code = $1
        """,
        fund_code,
    )
    if row is None:
        return None
    return (str(row["fund_name"]), str(row["fund_type"]))


def master_first_identity_resolver(
    conn_factory: Callable[[str], Awaitable[Connection]],
    fallback: Callable[[str], Awaitable[tuple[str, str]]],
) -> Callable[[str], Awaitable[tuple[str, str]]]:
    """Wrap the provider identity resolver with a master-table fast path."""

    async def resolver(fund_code: str) -> tuple[str, str]:
        async with conn_factory(fund_code) as conn:
            master = await lookup_fund_master_identity(conn, fund_code)
        if master is not None:
            return master
        return await fallback(fund_code)

    return resolver

"""Trading-calendar backfill CLI (2024–2026, SSE).

Usage:  python -m backend.market.calendar
Fetches the official Sina exchange calendar (akshare), marks 2024→today's
workdays as trading days / missing workdays as holidays, and uses a workday
fallback for the not-yet-published future window. Idempotent (upsert).
"""

from __future__ import annotations

import asyncio
import logging

from backend.common.database import create_pool
from backend.config import get_settings
from backend.market import service


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    pool = await create_pool(
        settings.worker_database_url,
        min_size=1,
        max_size=2,
    )
    try:
        inserted = await service.fill_trading_calendar(pool)
        print(f"trading calendar: {inserted} rows upserted (2024-01-01..2026-12-31)")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

"""手动触发 POST_MARKET 任务（验证四时段任务用）。

复用 worker 的 lease runner，为所有启用了 POST_MARKET 的租户生成报告，
并写入 reporting.reports + workflow.outbox（REPORT_PUBLISHED）。

用法: .venv/bin/python scripts/manual_trigger_post_market.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

# File-path execution sets sys.path[0] to ``scripts/``. Resolve the checkout
# from this file so the documented command works in /opt/wealthsail, Windows
# development checkouts, and any future deployment location.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.common.database import (  # noqa: E402
    create_pool,
    verify_runtime_login_boundary,
)
from backend.config import get_settings, validate_runtime_settings  # noqa: E402
from backend.jobs import runner  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main() -> None:
    settings = validate_runtime_settings(get_settings(), process="worker")
    pool = await create_pool(
        settings.worker_database_url,
        min_size=1,
        max_size=2,
        statement_cache_size=settings.db_statement_cache_size,
        runtime_role="wealthsail_worker",
    )
    try:
        # Match the long-running Worker trust boundary before any privileged
        # scheduler query reaches workflow/reporting tables.
        await verify_runtime_login_boundary(
            pool,
            process="worker",
            skip=not settings.is_production,
        )
        now = datetime.now()
        outcomes = await runner.run_session(pool, settings, "POST_MARKET", now)
        print(f"POST_MARKET_OUTCOMES={outcomes}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

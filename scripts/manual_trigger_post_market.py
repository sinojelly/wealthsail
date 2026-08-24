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

sys.path.insert(0, "/home/lenovo/wealthsail")

from backend.common.database import create_pool  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.jobs import runner  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main() -> None:
    settings = get_settings()
    pool = await create_pool(
        settings.worker_database_url, min_size=1, max_size=2
    )
    try:
        now = datetime.now()
        outcomes = await runner.run_session(pool, settings, "POST_MARKET", now)
        print(f"POST_MARKET_OUTCOMES={outcomes}")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

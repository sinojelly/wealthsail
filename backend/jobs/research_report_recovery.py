"""Recover a stored model-success/report-failure without another model call.

Preview: python -m backend.jobs.research_report_recovery --run-id UUID
Publish: add --publish after migration 108; an exact run and audit file are required.
"""

import argparse
import asyncio
import json
from uuid import UUID

from backend.common.database import create_pool, system_connection
from backend.config import get_settings
from backend.dependencies import now_shanghai
from backend.macro.research_report_recovery import recover_report


async def run(run_id, *, publish=False):
    settings = get_settings()
    pool = await create_pool(
        settings.worker_database_url, min_size=1, max_size=2,
        runtime_role="wealthsail_worker",
    )
    try:
        async with system_connection(pool) as conn:
            return await recover_report(
                conn, settings, run_id, now=now_shanghai(), publish=publish, refresh_nav=publish,
            )
    finally:
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.run_id, publish=args.publish))
    except Exception as error:  # noqa: BLE001 -- audit contents and credentials stay local
        print(json.dumps({"published": False, "errorClass": type(error).__name__}))
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

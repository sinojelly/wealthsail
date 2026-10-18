"""Consume explicit development requests without delivering reports or trades."""

import argparse
import asyncio
import json
import logging
from uuid import UUID, uuid4

from backend.common.database import (
    create_pool,
    system_connection,
    verify_runtime_login_boundary,
)
from backend.config import get_settings
from backend.dependencies import now_shanghai
from backend.jobs.alpha_public import sync_alpha_public
from backend.jobs.daily_research import sync_daily_research

logger = logging.getLogger("wealthsail.research_debug")


def enabled(settings):
    return (
        getattr(settings, "app_env", None) in {"development", "test"}
        and getattr(settings, "daily_research_debug_enabled", False)
        and getattr(settings, "daily_research_enabled", False)
    )


async def _claim(pool, now):
    from backend.macro.research_debug_repository import claim_refresh

    async with system_connection(pool) as conn:
        async with conn.transaction():
            return await claim_refresh(conn, now)


async def _finish(pool, identity, now, **kwargs):
    from backend.macro.research_debug_repository import finish_refresh

    async with system_connection(pool) as conn:
        await finish_refresh(conn, identity, now, **kwargs)


async def _operator_active(pool, request):
    async with system_connection(pool) as conn:
        return await conn.fetchval(
            """SELECT EXISTS(SELECT 1 FROM identity.tenant_members m
          JOIN identity.tenants t ON t.id=m.tenant_id
          WHERE m.tenant_id=$1 AND m.user_id=$2 AND m.status='ACTIVE'
          AND m.role IN ('OWNER','ADMIN') AND t.status='ACTIVE')""",
            request["tenant_id"],
            request["user_id"],
        )


async def sync_debug_refresh(pool, settings):
    if not enabled(settings):
        return {"status": "DISABLED"}
    started = now_shanghai()
    request = await _claim(pool, started)
    if request is None:
        return {"status": "IDLE"}
    identity = request["id"]
    status, error, batch, report = "FAILED", None, None, None
    try:
        if not await _operator_active(pool, request):
            raise PermissionError("operator membership revoked")
        collected = await sync_alpha_public(
            pool, now_shanghai(), debug_request_id=identity
        )
        batch = collected.get("batchId")
        if collected["status"] not in {"READY", "PARTIAL"} or not batch:
            raise ValueError("debug public collection unavailable")
        if now_shanghai() < started:
            raise ValueError("debug clock regressed")
        if not await _operator_active(pool, request):
            raise PermissionError("operator membership revoked")
        result = await sync_daily_research(
            pool,
            settings,
            now_shanghai(),
            debug_request_id=identity,
            source_batch_id=batch,
        )
        report = result.get("runId")
        status = (
            result["status"]
            if result["status"] in {"READY", "FAILED", "UNCERTAIN"}
            else "UNCERTAIN"
        )
        if status != "READY":
            error = "ResearchUnavailable"
    except asyncio.CancelledError:
        status, error = "UNCERTAIN", "CancelledError"
        raise
    except TimeoutError:
        status, error = "UNCERTAIN", "TimeoutError"
    except Exception as exc:  # noqa: BLE001 -- never persist provider bodies or credentials
        status, error = "FAILED", type(exc).__name__
        logger.warning("debug_research_failed error_class=%s", error)
    finally:
        await _finish(
            pool,
            identity,
            max(started, now_shanghai()),
            status=status,
            batch_id=batch,
            report_run_id=report,
            error_class=error,
        )
    return {
        "status": status,
        "requestId": str(identity),
        "batchId": batch,
        "reportRunId": report,
    }


async def _wait(stop_event, seconds):
    try:
        await asyncio.wait_for(stop_event.wait(), seconds)
    except TimeoutError:
        pass


async def run_debug_research_service(pool, settings, *, stop_event=None, wait=_wait):
    if not enabled(settings):
        return
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        try:
            await sync_debug_refresh(pool, settings)
        except Exception as exc:  # noqa: BLE001 -- claims are not retried after uncertain work
            logger.warning("debug_service_failed error_class=%s", type(exc).__name__)
        await wait(stop_event, 3)


async def enqueue_cli(pool, *, email, command_key):
    from backend.macro.research_debug_repository import enqueue_refresh

    async with system_connection(pool) as conn:
        rows = await conn.fetch(
            """SELECT m.tenant_id,m.user_id FROM identity.tenant_members m
          JOIN identity.profiles p ON p.id=m.user_id JOIN identity.tenants t ON t.id=m.tenant_id
          WHERE p.email=$1 AND m.status='ACTIVE' AND m.role IN ('OWNER','ADMIN') AND t.status='ACTIVE'
          ORDER BY m.tenant_id""",
            email,
        )
        if len(rows) != 1:
            raise ValueError(
                "local debug operator must have one explicit active owner/admin tenant"
            )
        async with conn.transaction():
            return await enqueue_refresh(
                conn,
                rows[0]["tenant_id"],
                rows[0]["user_id"],
                command_key,
                now_shanghai(),
            )


async def main(argv=None):
    from backend.jobs.macro_worker import validate_macro_settings
    from backend.macro.research_debug_repository import read_refresh

    parser = argparse.ArgumentParser(
        description="Queue one explicit local research refresh; no delivery"
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--request-key", type=UUID, default=None)
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args(argv)
    settings = validate_macro_settings(get_settings())
    if not enabled(settings):
        raise ValueError(
            "development research debug and daily research must be enabled"
        )
    pool = await create_pool(
        settings.worker_database_url,
        min_size=1,
        max_size=2,
        runtime_role="wealthsail_worker",
    )
    try:
        await verify_runtime_login_boundary(pool, process="worker")
        command_key = args.request_key or uuid4()
        result = await enqueue_cli(pool, email=args.email, command_key=command_key)
        print(
            json.dumps(
                {**result, "commandKey": str(command_key)},
                ensure_ascii=False,
                default=str,
            ),
            flush=True,
        )
        if args.no_wait:
            return 0
        # The running macro Worker consumes the queue. This command never
        # races it by launching a second model process locally.
        deadline = asyncio.get_running_loop().time() + 35 * 60
        while (
            result["status"] in {"QUEUED", "RUNNING"}
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(3)
            async with system_connection(pool) as conn:
                row = await conn.fetchrow(
                    "SELECT tenant_id,user_id FROM research.daily_research_refresh_requests WHERE id=$1",
                    UUID(result["id"]),
                )
                result = await read_refresh(
                    conn,
                    row["tenant_id"],
                    row["user_id"],
                    UUID(result["id"]),
                    now_shanghai(),
                )
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0 if result["status"] == "READY" else 1
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception as exc:  # noqa: BLE001 -- never print DSNs or provider messages
        logger.error("debug_cli_failed error_class=%s", type(exc).__name__)
        raise SystemExit(1) from None

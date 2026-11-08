"""Consume explicit research requests without delivering reports or trades."""

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
from backend.jobs.research_budget import research_request_budget
from backend.macro.research_diagnostics import safe_error_class
from backend.macro.research_modes import (
    preferred_research_request_mode,
    research_request_mode_allowed,
)

logger = logging.getLogger("wealthsail.research_debug")
REQUEST_TIMEOUT_SECONDS = 35 * 60


def enabled(settings):
    return preferred_research_request_mode(settings) is not None


async def _claim(pool, now, *, allowed_modes=None):
    from backend.macro.research_debug_repository import claim_refresh

    async with system_connection(pool) as conn:
        async with conn.transaction():
            return await claim_refresh(conn, now, allowed_modes=allowed_modes)


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


async def _stage(pool, request_id, stage):
    from backend.macro.research_debug_repository import set_refresh_stage

    async with system_connection(pool) as conn:
        return await set_refresh_stage(conn, request_id, now_shanghai(), stage)


async def sync_debug_refresh(pool, settings):
    if not enabled(settings):
        return {"status": "DISABLED"}
    started = now_shanghai()
    allowed_modes = [
        mode
        for mode in ("MANUAL", "DEBUG")
        if research_request_mode_allowed(settings, mode)
    ]
    request = await _claim(pool, started, allowed_modes=allowed_modes)
    if request is None:
        return {"status": "IDLE"}
    with research_request_budget(REQUEST_TIMEOUT_SECONDS):
        return await _run_request(pool, settings, request, started)


async def _run_request(pool, settings, request, started):
    identity, mode = request["id"], request.get("request_mode", "DEBUG")
    status, error, batch, report = "FAILED", None, None, None
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
            if not research_request_mode_allowed(settings, mode):
                raise PermissionError("saved request mode is disabled")
            if not await _operator_active(pool, request):
                raise PermissionError("operator membership revoked")
            if not await _stage(pool, identity, "COLLECTING"):
                raise PermissionError("research request is no longer running")
            request_arguments = (
                {"manual_request_id": identity}
                if mode == "MANUAL"
                else {"debug_request_id": identity}
            )
            collected = await sync_alpha_public(
                pool, now_shanghai(), **request_arguments
            )
            batch = collected.get("batchId")
            if collected["status"] not in {"READY", "PARTIAL"} or not batch:
                raise ValueError("public collection unavailable")
            if now_shanghai() < started:
                raise ValueError("research clock regressed")
            if not await _operator_active(pool, request):
                raise PermissionError("operator membership revoked")
            result = await sync_daily_research(
                pool,
                settings,
                now_shanghai(),
                source_batch_id=batch,
                **request_arguments,
            )
            report = result.get("runId")
            status = (
                result["status"]
                if result["status"] in {"READY", "FAILED", "UNCERTAIN"}
                else "UNCERTAIN"
            )
            if status != "READY":
                error = (
                    safe_error_class(result.get("errorClass")) or "ResearchUnavailable"
                )
    except asyncio.CancelledError:
        status, error = "UNCERTAIN", "CancelledError"
        raise
    except TimeoutError:
        status, error = "UNCERTAIN", "TimeoutError"
    except Exception as exc:  # noqa: BLE001 -- never persist provider bodies or credentials
        status, error = "FAILED", safe_error_class(type(exc).__name__)
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
    if not getattr(settings, "daily_research_enabled", False):
        from backend.macro.research_audit import check_research_audit

        check_research_audit(settings)
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
    if not research_request_mode_allowed(settings, "DEBUG"):
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

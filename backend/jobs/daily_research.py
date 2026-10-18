"""Daily public Codex research: python -m backend.jobs.daily_research --once."""

import argparse
import asyncio
from datetime import datetime, timedelta
import hashlib
import json
import logging

from backend.common.database import (
    create_pool,
    system_connection,
    verify_runtime_login_boundary,
)
from backend.config import get_settings
from backend.dependencies import now_shanghai
from backend.integrations.codex_research import preflight_codex, run_codex_research
from backend.integrations.daily_sources import (
    collect_daily_sources,
    verify_research_sources,
)
from backend.macro.daily_repository import (
    claim_daily_run,
    consumed,
    finish_daily_run,
    local,
    next_run,
    read_daily_research,
    read_previous_daily_research,
    read_recent_public_research,
    scheduled_for,
)
from backend.macro.daily_research import (
    PROMPT_VERSION,
    REPORT_SCHEMA,
    build_prompt,
    make_research_packet,
    validate_report,
)
from backend.macro.research_audit import write_research_audit

logger = logging.getLogger("wealthsail.daily_research")


async def load_public_packet(conn, now, external, *, debug_request_id=None):
    """Only shared research queries; never assemble an actor/portfolio context."""
    from backend.investment import repository
    from backend.investment.opportunity_service import market_evidence
    from backend.investment.service import analysis, market_context
    from backend.macro import market_repository
    from backend.macro.alpha_repository import read_alpha_public, merge_alpha_series
    from backend.investment.theme_graph import read_themes, theme_plans
    from backend.investment.strategy_memory import read_strategy_memory, public_memory
    from backend.macro.alpha_research import extend_public_packet
    from uuid import UUID

    context = await market_context(conn, now=now)
    permissions = await market_repository.read_permissions(conn)
    collected = await market_repository.read_collection(conn, now)
    series = market_repository.series_context(
        await market_repository.read_series(conn, now),
        now,
        permissions=permissions,
        source_results=collected.get("sourceResults", {}),
    )
    indices = await conn.fetch(
        "SELECT * FROM reference.research_indices WHERE id IN ('hs300','a500','zz500') ORDER BY sort_order,id"
    )
    evidence = []
    for index in indices:
        result = await analysis(conn, "index", index["id"])
        retrieved = market_repository._stamp(result.get("retrievedAt"))
        if (
            retrieved
            and retrieved > now
            or any(
                market_repository._day(point.get("date")) is None
                or market_repository._day(point["date"]) > now.date()
                for point in result.get("points", [])
            )
        ):
            result = {"state": "UNVERIFIED"}
        row = market_evidence(index, result)
        row["actionableZones"] = result.get("actionableZones")
        evidence.append(row)
    bundle = await read_alpha_public(
        conn,
        now,
        **(
            {"debug_request_id": debug_request_id}
            if debug_request_id is not None
            else {}
        ),
    )
    series = merge_alpha_series(series, bundle, now)
    themes = await read_themes(conn, now)
    rules = []
    settings = get_settings()
    if settings.opportunity_tenant_id and settings.opportunity_user_id:
        tenant, user = (
            UUID(settings.opportunity_tenant_id),
            UUID(settings.opportunity_user_id),
        )
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM identity.tenant_members WHERE tenant_id=$1 AND user_id=$2 AND status='ACTIVE')",
            tenant,
            user,
        ):
            rules = public_memory(
                await read_strategy_memory(conn, tenant, user, now), now
            )
    directions = [dict(row) for row in await repository.directions(conn)]
    previous = await read_daily_research(conn, now)
    previous["history"] = await read_recent_public_research(conn, now)
    price_previous = await read_previous_daily_research(conn, now)
    packet = make_research_packet(
        context,
        series,
        evidence,
        directions,
        now=now,
        previous=previous,
        external=external,
    )
    packet.update(
        extend_public_packet(
            bundle, themes, theme_plans(themes, bundle, now), rules, price_previous, now
        )
    )
    from backend.investment.discovery import build_discovery_packet

    packet.update(
        await build_discovery_packet(conn, packet, bundle, themes, price_previous, now)
    )
    return packet


async def sync_daily_research(
    pool, settings, now, *, debug_request_id=None, source_batch_id=None
):
    stamp = local(now)
    debug = debug_request_id is not None
    if debug and (
        getattr(settings, "app_env", None) not in {"development", "test"}
        or not getattr(settings, "daily_research_debug_enabled", False)
    ):
        return {"status": "DISABLED"}
    if debug and source_batch_id is None:
        raise ValueError("debug research requires its collected source batch")
    if not settings.daily_research_enabled:
        return {"status": "DISABLED"}
    if not debug and stamp < scheduled_for(stamp):
        return {"status": "NOT_DUE"}
    if not debug:
        async with system_connection(pool) as conn:
            if await consumed(conn, stamp):
                return {"status": "SKIPPED"}
    if not debug and getattr(settings, "alpha_public_enabled", False):
        from backend.jobs.alpha_public import sync_alpha_public

        await sync_alpha_public(pool, stamp)
        async with system_connection(pool) as conn:
            batch = await conn.fetchrow(
                "SELECT status,claimed_at FROM market.alpha_public_batches WHERE run_date=$1 AND run_mode='SCHEDULED'",
                stamp.date(),
            )
        current = local(now_shanghai())
        if (
            batch
            and batch["status"] == "CLAIMED"
            and current - batch["claimed_at"] < timedelta(minutes=10)
        ):
            return {"status": "WAITING_PUBLIC_DATA"}
    nav_error = None
    if not debug:
        from backend.investment.discovery import collect_discovery_nav_histories

        try:
            await collect_discovery_nav_histories(pool, local(now_shanghai()))
        except Exception as exc:  # noqa: BLE001 -- keep available research when one collection fails
            nav_error = type(exc).__name__
    external = await collect_daily_sources(stamp)
    if nav_error:
        external.setdefault("sourceResults", {})["CORE_FUND_NAV"] = {
            "status": "FAILED",
            "errorClass": nav_error,
        }
    # HTTP finishes first: its observations are never backdated to 08:30.
    async with system_connection(pool) as conn:
        packet = await load_public_packet(
            conn,
            local(now_shanghai()),
            external,
            **({"debug_request_id": debug_request_id} if debug else {}),
        )
    prompt = build_prompt(packet)
    unavailable, cli = None, {}
    try:
        cli = await preflight_codex(settings.daily_research_codex_path)
    except Exception as exc:  # noqa: BLE001 -- safe preflight failure, no gateway fallback
        unavailable = type(exc).__name__
    dispatched = local(now_shanghai())
    packet_day = local(datetime.fromisoformat(packet["asOf"])).date()
    if (
        dispatched < stamp
        or packet_day != dispatched.date()
        or (
            not debug
            and (dispatched.date() != stamp.date() or dispatched < scheduled_for(stamp))
        )
    ):
        return {"status": "SKIPPED"}
    async with system_connection(pool) as conn:
        claim = await claim_daily_run(
            conn,
            dispatched,
            packet,
            input_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            prompt_version=PROMPT_VERSION,
            cli_version=cli.get("version"),
            **(
                {
                    "debug_request_id": debug_request_id,
                    "source_batch_id": source_batch_id,
                }
                if debug
                else {}
            ),
        )
    if claim is None:
        return {"status": "SKIPPED"}
    # The unique slot has committed and the connection is released before exec.
    run_id = claim["run_id"]
    result = {"status": "FAILED", "runId": str(run_id)}
    payload, error = None, unavailable
    raw, checks, audit_error = None, None, None
    try:
        if unavailable:
            return result
        raw = await run_codex_research(
            cli["executable"],
            prompt,
            REPORT_SCHEMA,
            timeout=settings.daily_research_timeout_seconds,
            model=settings.daily_research_model or cli.get("selectedModel"),
        )
        write_research_audit(
            settings,
            run_id,
            raw=raw,
            stage="MODEL_RETURNED",
            completed_at=local(now_shanghai()),
        )
        checks = await verify_research_sources(
            raw.get("sources", []), now=local(now_shanghai())
        )
        write_research_audit(
            settings,
            run_id,
            raw=raw,
            source_checks=checks,
            stage="SOURCES_CHECKED",
            completed_at=local(now_shanghai()),
        )
        payload = validate_report(
            raw, packet, checks, completed_at=local(now_shanghai())
        )
        result["status"], error = "READY", None
    except asyncio.CancelledError as exc:
        audit_error = exc
        result["status"], error = "UNCERTAIN", "CancelledError"
        raise
    except TimeoutError as exc:
        audit_error = exc
        result["status"], error = "UNCERTAIN", "TimeoutError"
    except Exception as exc:  # noqa: BLE001 -- normal logs expose only error classes
        audit_error = exc
        error = type(exc).__name__
        logger.warning("daily_research_failed error_class=%s", error)
    finally:
        if raw is not None:
            write_research_audit(
                settings,
                run_id,
                raw=raw,
                source_checks=checks,
                stage=(
                    "READY"
                    if result["status"] == "READY"
                    else "VALIDATION_FAILED"
                    if checks is not None
                    else "SOURCE_CHECK_FAILED"
                ),
                completed_at=local(now_shanghai()),
                error=audit_error,
            )
        async with system_connection(pool) as conn:
            await finish_daily_run(
                conn,
                run_id,
                local(now_shanghai()),
                status=result["status"],
                payload=payload,
                error_class=error,
            )
    return result


async def _wait(stop_event, seconds):
    try:
        await asyncio.wait_for(stop_event.wait(), seconds)
    except TimeoutError:
        pass


async def run_daily_research_service(
    pool, settings, *, stop_event=None, now_provider=now_shanghai, wait=_wait
):
    if not settings.daily_research_enabled:
        return
    stop_event = stop_event or asyncio.Event()
    current = local(now_provider())
    due = scheduled_for(current)
    if current > due and not settings.daily_research_catch_up:
        due += timedelta(days=1)
    while not stop_event.is_set():
        current = local(now_provider())
        delay = (due - current).total_seconds()
        if delay > 0:
            await wait(stop_event, delay)
            continue
        try:
            outcome = await sync_daily_research(pool, settings, current)
            if outcome.get("status") == "WAITING_PUBLIC_DATA":
                due = local(now_provider()) + timedelta(seconds=60)
                continue
        except Exception as exc:  # noqa: BLE001 -- daily failures do not stop other services
            logger.warning(
                "daily_research_service_failed error_class=%s", type(exc).__name__
            )
        due = next_run(local(now_provider()))


async def main(argv=None):
    from backend.jobs.macro_worker import validate_macro_settings

    parser = argparse.ArgumentParser(description="One daily public Codex research slot")
    parser.add_argument("--once", action="store_true", required=True)
    parser.parse_args(argv)
    settings = validate_macro_settings(get_settings())
    pool = await create_pool(
        settings.worker_database_url,
        min_size=1,
        max_size=settings.db_pool_max_size,
        statement_cache_size=settings.db_statement_cache_size,
        runtime_role="wealthsail_worker",
    )
    try:
        await verify_runtime_login_boundary(pool, process="worker")
        result = await sync_daily_research(pool, settings, now_shanghai())
        print(json.dumps(result, ensure_ascii=False))
        return (
            0 if result["status"] in {"READY", "SKIPPED", "NOT_DUE", "DISABLED"} else 1
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    except Exception as exc:  # noqa: BLE001 -- runtime error messages can contain credentials
        logger.error("daily_research_failed error_class=%s", type(exc).__name__)
        raise SystemExit(1) from None

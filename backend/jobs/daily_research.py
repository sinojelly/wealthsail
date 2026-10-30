"""Daily public Codex research: python -m backend.jobs.daily_research --once."""

import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
import hashlib
import json
import logging
from time import monotonic

from backend.ai_usage.tracker import trace_inference, track_task
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
    is_scheduled_day,
)
from backend.macro.daily_research import (
    PROMPT_VERSION,
    REPORT_SCHEMA,
    build_prompt,
    make_research_packet,
    validate_report,
)
from backend.macro.research_audit import write_research_audit
from backend.jobs.research_inputs import prepare_research_inputs, research_catalysts

logger = logging.getLogger("wealthsail.daily_research")
PIPELINE_HEARTBEAT_SECONDS = 30


def _canonical_reason_code(exc: Exception) -> str:
    """Map an exception to a sanitized, bounded diagnostic reason code.

    Avoids leaking prompt text, sensitive URLs, or unparsed model payloads into logs.
    If the exception provides a structured `reason_code` (e.g. ResearchValidationError),
    it is returned directly to pinpoint the validation failure rule.

    Args:
        exc: Exception raised during research execution.

    Returns:
        Canonical string token representing failure category.
    """
    code = getattr(exc, "reason_code", None)
    if code and isinstance(code, str):
        return code

    name = type(exc).__name__
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "TIMEOUT"
    if isinstance(exc, (ConnectionError, OSError)):
        return "NETWORK_UNAVAILABLE"
    if "JSON" in name or "ValidationError" in name:
        return "SCHEMA_VALIDATION_FAILED"
    if isinstance(exc, ValueError):
        return "PAYLOAD_VALIDATION_FAILED"
    return name


async def load_public_packet(
    conn, now, external, *, debug_request_id=None, source_batch_id=None
):
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
        **({"source_batch_id": source_batch_id} if source_batch_id is not None else {}),
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
        await build_discovery_packet(
            conn,
            packet,
            bundle,
            themes,
            price_previous,
            now,
            catalysts=research_catalysts(bundle, external),
            **(
                {"frozen_histories": bundle.get("fundHistories", [])}
                if source_batch_id is not None
                else {}
            ),
        )
    )
    from backend.macro.institutional_packet import institutional_packet

    packet["institutionalSignals"] = institutional_packet(
        (packet.get("alphaPublic") or {}).get("catalysts", []),
        external.get("etfFlowSignals", {}),
        now,
    )
    return packet


class PipelineOwnershipLost(RuntimeError):
    pass


class PipelineRuntimeExpired(TimeoutError):
    pass


class SourceCollectionTimeout(TimeoutError):
    pass


def _schedule(settings):
    return {
        "schedule_mode": getattr(settings, "daily_research_schedule_mode", "DAILY"),
        "schedule_time": getattr(settings, "daily_research_schedule_time", "07:00"),
    }


def _remaining_runtime(claim, settings, now):
    # The first owner freezes this absolute deadline. A restarted worker's
    # current settings must neither shorten nor extend the original budget.
    deadline = claim["runtime_deadline_at"]
    if isinstance(deadline, str):
        deadline = datetime.fromisoformat(deadline)
    return (local(deadline) - local(now)).total_seconds()


@asynccontextmanager
async def _pipeline_heartbeat(pool, claim):
    """Cancel this owner on lease loss; never renew an expired or foreign token."""
    from backend.macro.daily_pipeline_repository import renew_pipeline_lease

    owner = asyncio.current_task()
    lost = False

    async def heartbeat():
        nonlocal lost
        try:
            while True:
                await asyncio.sleep(PIPELINE_HEARTBEAT_SECONDS)
                async with system_connection(pool) as conn:
                    owned = await renew_pipeline_lease(
                        conn, claim, local(now_shanghai())
                    )
                if not owned:
                    raise PipelineOwnershipLost()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            lost = True
            logger.warning(
                "daily_pipeline_heartbeat_lost pipeline_id=%s error_class=%s",
                claim["id"],
                type(exc).__name__,
            )
            owner.cancel()

    task = asyncio.create_task(heartbeat())

    async def stop():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    claim["_stop_heartbeat"] = stop
    try:
        yield
    except asyncio.CancelledError:
        if lost:
            raise PipelineOwnershipLost() from None
        raise
    finally:
        claim.pop("_stop_heartbeat", None)
        await stop()


async def _stop_pipeline_heartbeat(claim):
    # A terminal transaction changes RUNNING to a terminal state, so a renewal
    # after COMMIT would correctly return false. Join the heartbeat first; the
    # terminal write still checks the live lease token inside its transaction.
    stop = claim.get("_stop_heartbeat")
    if stop:
        await stop()


async def _pipeline_stage(pool, claim, *, stage, state="RUNNING", **fields):
    from backend.macro.daily_pipeline_repository import advance_pipeline

    if state != "RUNNING" or stage == "WAITING_SOURCES":
        await _stop_pipeline_heartbeat(claim)
    async with system_connection(pool) as conn:
        updated = await advance_pipeline(
            conn, claim, local(now_shanghai()), stage=stage, state=state, **fields
        )
    if not updated:
        raise PipelineOwnershipLost("pipeline lease is no longer owned")
    clock = monotonic()
    elapsed = max(0, round((clock - claim.get("_stage_clock", clock)) * 1000))
    claim["_stage_clock"] = clock
    claim["stage"] = stage
    for field in ("source_batch_id", "research_run_id"):
        if fields.get(field):
            claim[field] = fields[field]
    logger.info(
        "daily_pipeline_stage pipeline_id=%s stage=%s state=%s elapsed_ms=%s source_batch_id=%s run_id=%s error_class=%s",
        claim["id"],
        stage,
        state,
        elapsed,
        claim.get("source_batch_id", "-"),
        claim.get("research_run_id", "-"),
        fields.get("error_class", "-"),
    )


@track_task(
    "DAILY_RESEARCH",
    lambda bound: "基金机会研究（手动）"
    if bound.get("debug_request_id") is not None
    else "基金机会研究（自动）",
)
async def sync_daily_research(
    pool,
    settings,
    now,
    *,
    debug_request_id=None,
    source_batch_id=None,
    observe_only=False,
):
    if debug_request_id is not None:
        return await _execute_daily_research(
            pool,
            settings,
            now,
            debug_request_id=debug_request_id,
            source_batch_id=source_batch_id,
        )
    if not settings.daily_research_enabled:
        return {"status": "DISABLED"}
    stamp = local(now)
    schedule = _schedule(settings)
    from backend.macro.daily_pipeline_repository import (
        archive_prior_pipelines,
        claim_pipeline,
        read_pipeline,
    )

    # Existing work keeps the plan it was born with. Changing worker.env may
    # change tomorrow's schedule, but must not shorten today's recovery window
    # or exclude a Saturday task originally started under DAILY mode.
    async with system_connection(pool) as conn:
        await archive_prior_pipelines(conn, stamp)
        existing = await read_pipeline(conn, stamp)
        if await consumed(conn, stamp):
            return {"status": "SKIPPED"}
    if not existing and not is_scheduled_day(
        stamp, schedule_mode=schedule["schedule_mode"]
    ):
        return {"status": "NON_WORKDAY"}
    due = existing.get("scheduledFor") if existing else None
    if isinstance(due, str):
        due = datetime.fromisoformat(due)
    due = (
        local(due)
        if due
        else scheduled_for(stamp, schedule_time=schedule["schedule_time"])
    )
    if existing and due.date() < stamp.date():
        # Yesterday's live owner may finish, but its batch can never be used to
        # start today's model. Expired old work was archived above.
        return {"status": "WAITING_PIPELINE_OWNER", "pipelineId": existing["id"]}
    if stamp < due:
        return {"status": "NOT_DUE"}
    window_end = (
        existing["startWindowEndsAt"]
        if existing
        else due
        + timedelta(
            minutes=getattr(settings, "daily_research_start_window_minutes", 120)
        )
    )
    if isinstance(window_end, str):
        window_end = datetime.fromisoformat(window_end)
    if stamp >= local(window_end):
        from backend.macro.daily_pipeline_repository import finish_source_wait_window

        async with system_connection(pool) as conn:
            await finish_source_wait_window(conn, stamp)
            if await consumed(conn, stamp):
                return {"status": "SKIPPED"}
            pending = await read_pipeline(conn, stamp)
        # A live owner may legitimately finish after the start window. Keep
        # polling for completion or lease expiry, without reclaiming any work.
        if (
            pending
            and pending["state"] == "RUNNING"
            and pending.get("researchRunId") is None
            and pending["stage"]
            in {"COLLECTING", "WAITING_SOURCES", "VALIDATING_INPUT"}
        ):
            return {"status": "WAITING_PIPELINE_OWNER", "pipelineId": pending["id"]}
        return {"status": "MISSED"}
    if observe_only:
        # CATCH_UP=false prevents restart recovery, not observation of an
        # existing owner through completion or the frozen window's closure.
        if existing and (
            existing["state"] == "RUNNING"
            or existing.get("errorClass") == "PipelineLeaseExpired"
        ):
            return {"status": "WAITING_PIPELINE_OWNER", "pipelineId": existing["id"]}
        return {"status": "SKIPPED"}
    async with system_connection(pool) as conn:
        claim = await claim_pipeline(
            conn,
            stamp,
            schedule_time=schedule["schedule_time"],
            start_window_minutes=getattr(
                settings, "daily_research_start_window_minutes", 120
            ),
            pipeline_timeout_seconds=getattr(
                settings, "daily_research_pipeline_timeout_seconds", 7200
            ),
        )
        if claim is None:
            # A restarted worker can arrive before the dead owner's lease has
            # expired. Recheck after the conflict (also covers a concurrent
            # first INSERT) and poll only pre-model work, never a consumed slot.
            if await consumed(conn, stamp):
                return {"status": "SKIPPED"}
            waiting = await read_pipeline(conn, stamp)
            if (
                waiting
                and waiting.get("researchRunId") is None
                and (
                    waiting.get("state") == "RUNNING"
                    or waiting.get("state") == "UNCERTAIN"
                    and waiting.get("errorClass") == "PipelineLeaseExpired"
                )
                and waiting.get("stage")
                in {"COLLECTING", "WAITING_SOURCES", "VALIDATING_INPUT"}
            ):
                return {"status": "WAITING_PIPELINE_OWNER", "pipelineId": waiting["id"]}
            return {"status": "SKIPPED"}
    claim = dict(claim)
    claim.setdefault("started_at", stamp)
    try:
        remaining = _remaining_runtime(claim, settings, stamp)
        if remaining <= 0:
            raise PipelineRuntimeExpired()
        claim["_runtime_deadline"] = monotonic() + remaining
        async with asyncio.timeout(remaining), _pipeline_heartbeat(pool, claim):
            return await _run_owned_pipeline(pool, settings, stamp, claim)
    except asyncio.CancelledError:
        if not claim.get("_terminal_written"):
            if claim.get("research_run_id"):
                await _pipeline_stage(
                    pool,
                    claim,
                    stage=claim["stage"],
                    state="UNCERTAIN",
                    error_class="CancelledError",
                )
            else:
                from backend.macro.daily_pipeline_repository import (
                    release_pipeline_before_model,
                )

                async with system_connection(pool) as conn:
                    await release_pipeline_before_model(
                        conn, claim, local(now_shanghai())
                    )
        raise
    except Exception as exc:
        logger.warning(
            "daily_pipeline_failed pipeline_id=%s stage=%s error_class=%s",
            claim["id"],
            claim["stage"],
            type(exc).__name__,
        )
        # A total-runtime timeout before dispatch is a known failed preparation.
        # After a committed model slot it remains uncertain and is never retried.
        state = (
            "UNCERTAIN"
            if isinstance(exc, PipelineOwnershipLost)
            or (isinstance(exc, TimeoutError) and claim.get("research_run_id"))
            else "FAILED"
        )
        error_class = (
            "PipelineRuntimeExpired"
            if type(exc) is TimeoutError
            else type(exc).__name__
        )
        if not isinstance(exc, PipelineOwnershipLost) and not claim.get(
            "_terminal_written"
        ):
            await _pipeline_stage(
                pool,
                claim,
                stage=claim["stage"],
                state=state,
                error_class=error_class,
            )
        return {
            "status": state,
            "errorClass": error_class,
        }


async def _run_owned_pipeline(pool, settings, stamp, claim):
    from backend.jobs.alpha_public import sync_alpha_public
    from backend.macro.alpha_repository import read_alpha_public
    from backend.macro.daily_pipeline_repository import (
        scheduled_source_batch,
        source_quality,
    )

    await _pipeline_stage(pool, claim, stage="COLLECTING")
    async with system_connection(pool) as conn:
        batch = await scheduled_source_batch(conn, local(now_shanghai()))
    # A restart must not launch another collector or overwrite a failed batch.
    # Existing CLAIMED batches remain owned by their original collector; an
    # orphaned collector therefore waits until the startup window closes.
    if batch is None:
        try:
            async with asyncio.timeout(900):
                await sync_alpha_public(pool, stamp)
        except TimeoutError:
            raise SourceCollectionTimeout() from None
    current = local(now_shanghai())
    if current.date() != stamp.date():
        await _pipeline_stage(
            pool,
            claim,
            stage="COLLECTING",
            state="MISSED",
            error_class="SourceCollectionCrossedDay",
        )
        return {"status": "MISSED", "errorClass": "SourceCollectionCrossedDay"}
    if batch is None:
        async with system_connection(pool) as conn:
            batch = await scheduled_source_batch(conn, current)
    if batch is None:
        raise ValueError("scheduled source batch is missing")
    if batch.get("run_date") != stamp.date():
        raise ValueError("scheduled source batch belongs to another date")
    batch_id = batch["id"]
    if claim.get("source_batch_id") and str(claim["source_batch_id"]) != str(batch_id):
        raise ValueError("pipeline source batch changed")
    if (
        batch["status"] == "CLAIMED"
        or not batch.get("completed_at")
        or batch["completed_at"] > current
    ):
        await _pipeline_stage(
            pool, claim, stage="WAITING_SOURCES", source_batch_id=batch_id
        )
        return {"status": "WAITING_PUBLIC_DATA", "pipelineId": str(claim["id"])}
    if batch["status"] not in {"READY", "PARTIAL"}:
        await _pipeline_stage(
            pool,
            claim,
            stage="VALIDATING_INPUT",
            state="FAILED",
            source_batch_id=batch_id,
            error_class="SourceCollectionFailed",
        )
        return {"status": "FAILED", "errorClass": "SourceCollectionFailed"}
    await _pipeline_stage(
        pool, claim, stage="VALIDATING_INPUT", source_batch_id=batch_id
    )
    async with system_connection(pool) as conn:
        bundle = await read_alpha_public(conn, current, source_batch_id=batch_id)
    quality = source_quality(bundle, current)
    await _pipeline_stage(pool, claim, stage="VALIDATING_INPUT", quality=quality)
    if not quality["allowed"]:
        await _pipeline_stage(
            pool,
            claim,
            stage="VALIDATING_INPUT",
            state="FAILED",
            error_class="SourceQualityRejected",
        )
        return {"status": "FAILED", "errorClass": "SourceQualityRejected"}
    claim["_source_bundle"] = bundle
    external = await collect_daily_sources(current)
    external = await prepare_research_inputs(
        pool, local(now_shanghai()), external, source_batch_id=batch_id
    )
    async with system_connection(pool) as conn:
        packet = await load_public_packet(
            conn, local(now_shanghai()), external, source_batch_id=batch_id
        )
    packet["sourceBatchId"] = str(batch_id)
    packet["inputQuality"] = quality
    result = await _execute_daily_research(
        pool,
        settings,
        stamp,
        source_batch_id=batch_id,
        packet=packet,
        pipeline=claim,
    )
    state = (
        result["status"]
        if result["status"] in {"READY", "FAILED", "UNCERTAIN", "MISSED"}
        else "UNCERTAIN"
    )
    if not claim.get("_terminal_written"):
        await _pipeline_stage(
            pool,
            claim,
            stage=claim["stage"],
            state=state,
            error_class=result.get("errorClass"),
        )
    return result


async def _execute_daily_research(
    pool,
    settings,
    now,
    *,
    debug_request_id=None,
    source_batch_id=None,
    packet=None,
    pipeline=None,
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
    if not debug:
        async with system_connection(pool) as conn:
            if await consumed(conn, stamp):
                return {"status": "SKIPPED"}
    if packet is None:
        external = await collect_daily_sources(stamp)
        external = await prepare_research_inputs(
            pool, local(now_shanghai()), external, debug_request_id=debug_request_id
        )
        async with system_connection(pool) as conn:
            packet = await load_public_packet(
                conn,
                local(now_shanghai()),
                external,
                **({"debug_request_id": debug_request_id} if debug else {}),
            )
    prompt = build_prompt(packet)
    unavailable, cli = None, {}
    remote_codex = bool(getattr(settings, "codex_runner_url", ""))
    try:
        if remote_codex:
            from backend.integrations import codex_client

            cli = await codex_client.preflight(settings)
        else:
            cli = await preflight_codex(settings.daily_research_codex_path)
    except Exception as exc:  # noqa: BLE001 -- safe preflight failure, no gateway fallback
        unavailable = type(exc).__name__
    if unavailable:
        return {"status": "FAILED", "errorClass": unavailable}
    dispatched = local(now_shanghai())
    if not debug and dispatched.date() != stamp.date():
        return {"status": "MISSED", "errorClass": "SourcePreparationCrossedDay"}
    schedule = _schedule(settings)
    due = pipeline.get("scheduled_for") if pipeline else None
    if isinstance(due, str):
        due = datetime.fromisoformat(due)
    due = (
        local(due)
        if due
        else scheduled_for(stamp, schedule_time=schedule["schedule_time"])
    )
    packet_day = local(datetime.fromisoformat(packet["asOf"])).date()
    if (
        dispatched < stamp
        or packet_day != dispatched.date()
        or (not debug and (dispatched.date() != stamp.date() or dispatched < due))
    ):
        return {"status": "SKIPPED"}
    if (
        not debug
        and not pipeline
        and dispatched
        >= due
        + timedelta(
            minutes=getattr(settings, "daily_research_start_window_minutes", 120)
        )
    ):
        return {"status": "MISSED", "errorClass": "StartWindowExpired"}
    if pipeline:
        from backend.macro.daily_pipeline_repository import source_quality

        if _remaining_runtime(pipeline, settings, dispatched) <= 0:
            raise PipelineRuntimeExpired()
        if not source_quality(pipeline.get("_source_bundle", {}), dispatched)[
            "allowed"
        ]:
            return {"status": "FAILED", "errorClass": "SourceQualityRejected"}
    async with system_connection(pool) as conn:

        async def dispatch():
            return await claim_daily_run(
                conn,
                dispatched,
                packet,
                input_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                prompt_version=PROMPT_VERSION,
                cli_version=cli.get("version"),
                source_batch_id=source_batch_id,
                **({"schedule_time": due.strftime("%H:%M")} if not debug else {}),
                **({"debug_request_id": debug_request_id} if debug else {}),
            )

        if pipeline:
            from backend.macro.daily_pipeline_repository import advance_pipeline

            async with conn.transaction():
                if not await advance_pipeline(
                    conn, pipeline, dispatched, stage="MODEL_RUNNING"
                ):
                    raise PipelineOwnershipLost()
                claim = await dispatch()
                if claim is not None and not await advance_pipeline(
                    conn,
                    pipeline,
                    dispatched,
                    stage="MODEL_RUNNING",
                    research_run_id=claim["run_id"],
                ):
                    raise PipelineOwnershipLost()
        else:
            claim = await dispatch()
    if claim is None:
        return {"status": "SKIPPED"}
    # The unique slot has committed and the connection is released before exec.
    run_id = claim["run_id"]
    if pipeline:
        pipeline.update(stage="MODEL_RUNNING", research_run_id=run_id)
        logger.info(
            "daily_pipeline_stage pipeline_id=%s stage=MODEL_RUNNING state=RUNNING run_id=%s source_batch_id=%s",
            pipeline["id"],
            run_id,
            source_batch_id,
        )
    result = {"status": "FAILED", "runId": str(run_id)}
    payload, error = None, unavailable
    raw, checks, audit_error = None, None, None
    current_stage = "MODEL_EXECUTION"
    try:
        if unavailable:
            return result
        model = settings.daily_research_model or getattr(settings, "codex_model", None)
        async with asyncio.timeout(settings.daily_research_timeout_seconds):
            if remote_codex:
                raw = await codex_client.research(
                    settings,
                    prompt,
                    REPORT_SCHEMA,
                    timeout=settings.daily_research_timeout_seconds,
                    model=model,
                )
            else:
                selected_model = model or cli.get("selectedModel")
                raw = await trace_inference(
                    settings,
                    model=selected_model,
                    call_type="research",
                    operation=lambda: run_codex_research(
                        cli["executable"],
                        prompt,
                        REPORT_SCHEMA,
                        timeout=settings.daily_research_timeout_seconds,
                        model=selected_model,
                    ),
                )
        write_research_audit(
            settings,
            run_id,
            raw=raw,
            stage="MODEL_RETURNED",
            completed_at=local(now_shanghai()),
        )
        current_stage = "SOURCE_VERIFICATION"
        if pipeline:
            await _pipeline_stage(pool, pipeline, stage="VERIFYING_OUTPUT")
        sources = raw.get("sources", []) if isinstance(raw, dict) else []
        checks = await verify_research_sources(sources, now=local(now_shanghai()))
        write_research_audit(
            settings,
            run_id,
            raw=raw,
            source_checks=checks,
            stage="SOURCES_CHECKED",
            completed_at=local(now_shanghai()),
        )
        current_stage = "REPORT_VALIDATION"
        payload = validate_report(
            raw, packet, checks, completed_at=local(now_shanghai())
        )
        if isinstance(packet.get("inputQuality"), dict):
            payload = dict(payload)
            payload["inputQuality"] = packet["inputQuality"]
            payload["sourceBatchId"] = packet.get("sourceBatchId")
        result["status"], error = "READY", None
    except asyncio.CancelledError as exc:
        audit_error = exc
        result["status"], error = "UNCERTAIN", "CancelledError"
        if pipeline and monotonic() >= pipeline.get("_runtime_deadline", float("inf")):
            error = "PipelineRuntimeExpired"
        raise
    except TimeoutError as exc:
        audit_error = exc
        result["status"], error = "UNCERTAIN", "TimeoutError"
        reason_code = _canonical_reason_code(exc)
        logger.warning(
            "daily_research_failed error_class=%s stage=%s reason_code=%s run_id=%s",
            error,
            current_stage,
            reason_code,
            run_id,
        )
    except Exception as exc:  # noqa: BLE001
        audit_error = exc
        error = type(exc).__name__
        reason_code = _canonical_reason_code(exc)
        logger.warning(
            "daily_research_failed error_class=%s stage=%s reason_code=%s run_id=%s",
            error,
            current_stage,
            reason_code,
            run_id,
        )
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
        if pipeline and result["status"] == "READY":
            await _pipeline_stage(pool, pipeline, stage="PERSISTING")
        if pipeline:
            await _stop_pipeline_heartbeat(pipeline)
        async with system_connection(pool) as conn:

            async def persist_result():
                await finish_daily_run(
                    conn,
                    run_id,
                    local(now_shanghai()),
                    status=result["status"],
                    payload=payload,
                    error_class=error,
                )

            if pipeline:
                from backend.macro.daily_pipeline_repository import advance_pipeline

                async with conn.transaction():
                    if not await advance_pipeline(
                        conn,
                        pipeline,
                        local(now_shanghai()),
                        stage=pipeline["stage"],
                        state=result["status"],
                        error_class=error,
                    ):
                        raise PipelineOwnershipLost()
                    await persist_result()
                pipeline["_terminal_written"] = True
            else:
                await persist_result()
    if error:
        result["errorClass"] = error
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
    schedule = _schedule(settings)
    from backend.macro.daily_pipeline_repository import (
        archive_prior_pipelines,
        read_pipeline,
    )

    async with system_connection(pool) as conn:
        await archive_prior_pipelines(conn, current)
        existing = await read_pipeline(conn, current)
    due = existing.get("scheduledFor") if existing else None
    if isinstance(due, str):
        due = datetime.fromisoformat(due)
    due = (
        local(due)
        if due
        else scheduled_for(current, schedule_time=schedule["schedule_time"])
    )
    if not existing and not is_scheduled_day(
        current, schedule_mode=schedule["schedule_mode"]
    ):
        due = next_run(current, **schedule)
    observe_only = bool(
        existing and current > due and not settings.daily_research_catch_up
    )
    if not existing and current > due and not settings.daily_research_catch_up:
        due = next_run(current, **schedule)
    while not stop_event.is_set():
        current = local(now_provider())
        try:
            async with system_connection(pool) as conn:
                prior_pending = await archive_prior_pipelines(conn, current)
        except Exception as exc:  # noqa: BLE001 -- keep the original due time on observation failure
            logger.warning(
                "daily_research_observation_failed error_class=%s retry_seconds=60",
                type(exc).__name__,
            )
            await wait(stop_event, 60)
            continue
        delay = (due - current).total_seconds()
        if delay > 0:
            await wait(stop_event, min(delay, 60) if prior_pending else delay)
            continue
        try:
            outcome = await sync_daily_research(
                pool,
                settings,
                current,
                **({"observe_only": True} if observe_only else {}),
            )
            if outcome.get("status") in {
                "WAITING_PUBLIC_DATA",
                "WAITING_PIPELINE_OWNER",
            }:
                due = local(now_provider()) + timedelta(seconds=60)
                continue
        except Exception as exc:  # noqa: BLE001 -- daily failures do not stop other services
            logger.warning(
                "daily_research_service_failed error_class=%s", type(exc).__name__
            )
        observe_only = False
        due = next_run(local(now_provider()), **schedule)


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

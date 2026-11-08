"""Daily public Codex research: python -m backend.jobs.daily_research --once."""

import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
from copy import deepcopy
from datetime import datetime, timedelta
import errno
import hashlib
import json
import logging
import socket
from time import monotonic

from backend.ai_usage.tracker import (
    record_research_progress,
    research_progress_run,
    trace_inference,
    track_task,
)
from backend.common.database import (
    create_pool,
    system_connection,
    verify_runtime_login_boundary,
)
from backend.config import get_settings
from backend.dependencies import now_shanghai
from backend.integrations.codex_research import (
    CodexOutputError,
    preflight_codex,
    run_codex_research,
)
from backend.integrations.daily_sources import (
    collect_daily_sources,
    verify_research_sources,
)
from backend.integrations.research_execution import (
    sanitize_execution_progress,
    sanitize_execution_statistics,
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
    read_automatic_research_cooldown,
    scheduled_for,
    is_scheduled_day,
)
from backend.macro.daily_research import (
    PROMPT_VERSION,
    REPORT_SCHEMA,
    build_prompt,
    make_research_packet,
    ResearchValidationError,
    validate_report,
)
from backend.macro import research_acceptance_repository as archive_repository
from backend.macro.research_acceptance import (
    rejected_outcome,
    sources_for_verification,
    validate_report_outcome,
)
from backend.macro.research_repair import repair_report, repair_summary
from backend.macro.research_audit import (
    check_research_audit,
    write_research_audit,
    write_research_diagnostic,
)
from backend.macro.research_diagnostics import (
    REASON_CODES,
    safe_error_class,
    sanitize_failure_diagnostics,
)
from backend.jobs.research_inputs import prepare_research_inputs, research_catalysts
from backend.jobs.research_budget import model_budget_fits
from backend.jobs.research_exit_recovery import invocation_identity
from backend.integrations.research_lifecycle import (
    ResearchLifecycle,
    research_deadlines,
    sanitize_lifecycle,
)
from backend.macro.research_modes import research_request_mode_allowed
from backend.macro.model_dispatch_repository import (
    ResearchModelBusy,
    lock_model_dispatch,
    create_model_guard,
    release_model_guard,
)

logger = logging.getLogger("wealthsail.daily_research")
PIPELINE_HEARTBEAT_SECONDS = 30
RESEARCH_PROGRESS_SECONDS = 15


def _cooldown_result(cooldown):
    eligible = cooldown["automaticEligibleAt"]
    logger.info("daily_research_cooldown hours=23 automatic_eligible_at=%s", eligible.isoformat())
    return {"status": "COOLDOWN", "errorClass": "ResearchCooldownActive",
            "automaticEligibleAt": eligible.isoformat()}


def _canonical_reason_code(exc: Exception) -> str:
    """Map an exception to a sanitized, bounded diagnostic reason code.

    Avoids leaking prompt text, sensitive URLs, or unparsed model payloads into logs.
    If the exception provides a structured `reason_code` (e.g. ResearchValidationError),
    it is accepted only from the fixed reason-code set.

    Args:
        exc: Exception raised during research execution.

    Returns:
        Canonical string token representing failure category.
    """
    code = getattr(exc, "timeout_origin", None) or getattr(exc, "reason_code", None)
    if isinstance(code, str) and code in REASON_CODES:
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
    return "UNEXPECTED_ERROR"


def research_call_identity(run_id, owner_token):
    return invocation_identity(run_id, owner_token)


def _log_research_failure(exc, stage, run_id, *, reason_code=None):
    diagnostics = sanitize_failure_diagnostics(
        stage=stage,
        reason_code=reason_code or _canonical_reason_code(exc),
        rule_id=getattr(exc, "rule_id", None),
        item_index=getattr(exc, "item_index", None),
        missing_industry_codes=getattr(exc, "missing_industry_codes", None),
    )
    message = (
        "daily_research_failed error_class=%s stage=%s reason_code=%s "
        "rule_id=%s item_index=%s run_id=%s"
    )
    values = [
        safe_error_class(type(exc).__name__),
        diagnostics["failure_stage"] or "-",
        diagnostics["failure_reason_code"] or "-",
        diagnostics["failure_rule_id"] or "-",
        diagnostics["failure_item_index"]
        if diagnostics["failure_item_index"] is not None
        else "-",
        run_id,
    ]
    if codes := diagnostics.get("failure_industry_codes"):
        message += " industry_codes=%s"
        values.append(json.dumps(codes, separators=(",", ":")))
    logger.warning(message, *values)
    return diagnostics


def _write_diagnostic_summary(settings, run_id, diagnostics, error, status):
    write_research_diagnostic(
        settings,
        run_id,
        stage=diagnostics.get("failure_stage", "PERSISTING"),
        error_class=error,
        reason_code=diagnostics.get("failure_reason_code"),
        rule_id=diagnostics.get("failure_rule_id"),
        item_index=diagnostics.get("failure_item_index"),
        missing_industry_codes=diagnostics.get("failure_industry_codes"),
        report_status=status,
        completed_at=local(now_shanghai()),
    )


async def load_public_packet(
    conn,
    now,
    external,
    *,
    debug_request_id=None,
    manual_request_id=None,
    source_batch_id=None,
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
    from backend.jobs.opportunity_market import relative_strength_benchmark
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
    benchmark = relative_strength_benchmark(None, now)
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
        if index["id"] == "hs300":
            benchmark = relative_strength_benchmark(result, now)
    bundle = await read_alpha_public(
        conn,
        now,
        **({"source_batch_id": source_batch_id} if source_batch_id is not None else {}),
        **(
            {"debug_request_id": debug_request_id}
            if debug_request_id is not None
            else {}
        ),
        **(
            {"manual_request_id": manual_request_id}
            if manual_request_id is not None
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
    packet["relativeStrengthBenchmark"] = benchmark
    packet.update(
        extend_public_packet(
            bundle,
            themes,
            theme_plans(themes, bundle, now, benchmark_points=benchmark["points"]),
            rules,
            price_previous,
            now,
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
    from backend.macro.research_topic_repository import read_pending_topic_scopes
    from backend.macro.research_topics import freeze_discovery_packet

    request_id = manual_request_id or debug_request_id
    tenant_id = (
        await conn.fetchval(
            "SELECT tenant_id FROM research.daily_research_refresh_requests WHERE id=$1",
            UUID(str(request_id)),
        )
        if request_id is not None
        else None
    )
    # Scheduled research can consume public backlog only. Manual backlog is
    # scoped by the server-owned request identity, never client-supplied labels.
    if request_id is not None and tenant_id is None:
        raise ResearchValidationError(
            "PAYLOAD_VALIDATION_FAILED", "research request scope unavailable"
        )
    packet["pendingTopicScopes"] = await read_pending_topic_scopes(
        conn, tenant_id=tenant_id
    )
    return freeze_discovery_packet(packet, now)


class PipelineOwnershipLost(RuntimeError):
    pass


class PipelineRuntimeExpired(TimeoutError):
    pass


class SourceCollectionTimeout(TimeoutError):
    pass


class SchedulerDatabaseUnavailable(RuntimeError):
    """Retry permission for database scheduling work, never model execution."""

    def __init__(self, error):
        self.error_class = type(error).__name__
        super().__init__(self.error_class)


@asynccontextmanager
async def _scheduler_database_connection(pool):
    # Use only before a pipeline owner starts work. A DB error after model use
    # must not become permission to repeat research or terminal persistence.
    try:
        async with system_connection(pool) as conn:
            yield conn
    except Exception as exc:
        temporary_network = (
            isinstance(exc, socket.gaierror) and exc.errno == socket.EAI_AGAIN
        ) or (
            isinstance(exc, OSError)
            and exc.errno in {errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH}
        )
        if (
            isinstance(exc, (ConnectionError, TimeoutError))
            or temporary_network
            or getattr(exc, "sqlstate", None)
            in {
                "08000",
                "08001",
                "08003",
                "08006",  # connection unavailable/lost
                "53300",
                "57P01",
                "57P02",
                "57P03",  # capacity/restart
                "40001",
                "40P01",
                "55P03",  # serialization/deadlock/lock conflict
            }
        ):
            raise SchedulerDatabaseUnavailable(exc) from exc
        raise


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


async def _wait_model_slot():
    await asyncio.sleep(3)


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
    or bound.get("manual_request_id") is not None
    else "基金机会研究（自动）",
)
async def sync_daily_research(
    pool,
    settings,
    now,
    *,
    debug_request_id=None,
    manual_request_id=None,
    source_batch_id=None,
    observe_only=False,
):
    if debug_request_id is not None or manual_request_id is not None:
        return await _execute_daily_research(
            pool,
            settings,
            now,
            debug_request_id=debug_request_id,
            manual_request_id=manual_request_id,
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
    async with _scheduler_database_connection(pool) as conn:
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

        async with _scheduler_database_connection(pool) as conn:
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
    async with _scheduler_database_connection(pool) as conn:
        cooldown = await read_automatic_research_cooldown(conn, stamp)
        if cooldown["active"]:
            return _cooldown_result(cooldown)
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
        if claim.get("_persistence_failed"):
            # The atomic result/acceptance write failed after dispatch. Never
            # replace it with a separate pipeline-only terminal transition.
            raise
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
    state = result["status"]
    if state == "COOLDOWN":
        state = "MISSED"
    elif state not in {"READY", "FAILED", "UNCERTAIN", "MISSED"}:
        state = "UNCERTAIN"
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
    manual_request_id=None,
    source_batch_id=None,
    packet=None,
    pipeline=None,
):
    stamp = local(now)
    if debug_request_id is not None and manual_request_id is not None:
        raise ValueError("select one explicit research request mode")
    debug = debug_request_id is not None
    manual = manual_request_id is not None
    explicit = debug or manual
    if explicit and not research_request_mode_allowed(
        settings, "MANUAL" if manual else "DEBUG"
    ):
        return {"status": "DISABLED"}
    if explicit and source_batch_id is None:
        raise ValueError("explicit research requires its collected source batch")
    if not explicit and not settings.daily_research_enabled:
        return {"status": "DISABLED"}
    if not explicit:
        async with system_connection(pool) as conn:
            if await consumed(conn, stamp):
                return {"status": "SKIPPED"}
            cooldown = await read_automatic_research_cooldown(conn, stamp)
            if cooldown["active"]:
                return _cooldown_result(cooldown)
    manual_bundle = None
    if manual:
        from backend.macro.alpha_repository import read_alpha_public
        from backend.macro.daily_pipeline_repository import source_quality

        async with system_connection(pool) as conn:
            manual_bundle = await read_alpha_public(
                conn,
                stamp,
                manual_request_id=manual_request_id,
                source_batch_id=source_batch_id,
            )
        if (
            manual_bundle.get("batchId") != str(source_batch_id)
            or not source_quality(manual_bundle, stamp)["allowed"]
        ):
            return {"status": "FAILED", "errorClass": "SourceQualityRejected"}
    request_selection = (
        {"manual_request_id": manual_request_id, "source_batch_id": source_batch_id}
        if manual
        else {"debug_request_id": debug_request_id}
        if debug
        else {}
    )
    if packet is None:
        external = await collect_daily_sources(stamp)
        external = await prepare_research_inputs(
            pool,
            local(now_shanghai()),
            external,
            **(request_selection if explicit else {"debug_request_id": None}),
        )
        async with system_connection(pool) as conn:
            packet = await load_public_packet(
                conn,
                local(now_shanghai()),
                external,
                **request_selection,
            )
    if manual:
        packet = dict(packet)
        packet["inputQuality"] = source_quality(manual_bundle, local(now_shanghai()))
        packet["sourceBatchId"] = str(source_batch_id)
    prompt = build_prompt(packet)
    if (packet.get("researchDiscovery") or {}).get(
        "version"
    ) == "research-discovery-v2":
        from backend.macro.research_topics import BUDGET

        if len(prompt.encode("utf-8")) > BUDGET["maxInputBytes"]:
            raise ResearchValidationError(
                "PAYLOAD_VALIDATION_FAILED",
                "discovery input exceeds this batch's byte budget",
                rule_id="RESEARCH_INPUT_BUDGET_EXCEEDED",
            )
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
    deadlines = research_deadlines(settings.daily_research_timeout_seconds)
    while True:
        dispatched = local(now_shanghai())
        if not model_budget_fits(
            deadlines["workerSeconds"],
            pipeline_deadline=(pipeline or {}).get("_runtime_deadline", float("inf")),
        ):
            return {"status": "FAILED", "errorClass": "ResearchExecutionBudgetInsufficient"}
        if not explicit and dispatched.date() != stamp.date():
            return {"status": "MISSED", "errorClass": "SourcePreparationCrossedDay"}
        if (
            dispatched < stamp
            or packet_day != dispatched.date()
            or (not explicit and dispatched < due)
        ):
            return {"status": "SKIPPED"}
        if (
            not explicit
            and not pipeline
            and dispatched
            >= due
            + timedelta(
                minutes=getattr(settings, "daily_research_start_window_minutes", 120)
            )
        ):
            return {"status": "MISSED", "errorClass": "StartWindowExpired"}
        if pipeline or manual:
            from backend.macro.daily_pipeline_repository import source_quality

            if pipeline and _remaining_runtime(pipeline, settings, dispatched) <= 0:
                raise PipelineRuntimeExpired()
            if not source_quality(
                manual_bundle if manual else pipeline.get("_source_bundle", {}),
                dispatched,
            )["allowed"]:
                return {"status": "FAILED", "errorClass": "SourceQualityRejected"}
        try:
            async with system_connection(pool) as conn:
                # Occupation and the immutable cost barrier must commit together.
                # Neither a busy model nor a lost owner may consume a new slot.
                async with conn.transaction():
                    await lock_model_dispatch(conn)
                    if not explicit:
                        cooldown = await read_automatic_research_cooldown(conn, dispatched)
                        if cooldown["active"]:
                            return _cooldown_result(cooldown)
                    if pipeline:
                        from backend.macro.daily_pipeline_repository import (
                            advance_pipeline,
                        )

                        if not await advance_pipeline(
                            conn, pipeline, dispatched, stage="MODEL_RUNNING"
                        ):
                            raise PipelineOwnershipLost()
                    claim = await claim_daily_run(
                        conn,
                        dispatched,
                        packet,
                        input_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                        prompt_version=PROMPT_VERSION,
                        cli_version=cli.get("version"),
                        source_batch_id=source_batch_id,
                        **(
                            {"schedule_time": due.strftime("%H:%M")}
                            if not explicit
                            else {}
                        ),
                        **({"debug_request_id": debug_request_id} if debug else {}),
                        **({"manual_request_id": manual_request_id} if manual else {}),
                    )
                    owner_token = (
                        await create_model_guard(conn, claim["run_id"], dispatched)
                        if claim is not None
                        else None
                    )
                    if (
                        pipeline
                        and claim is not None
                        and not await advance_pipeline(
                            conn,
                            pipeline,
                            dispatched,
                            stage="MODEL_RUNNING",
                            research_run_id=claim["run_id"],
                        )
                    ):
                        raise PipelineOwnershipLost()
            break
        except ResearchModelBusy:
            if not explicit:
                async with system_connection(pool) as conn:
                    cooldown = await read_automatic_research_cooldown(conn, local(now_shanghai()))
                    if cooldown["active"]:
                        return _cooldown_result(cooldown)
            # Preserve request/run identities and release the DB connection while
            # waiting. The existing pipeline/consumer deadline still applies.
            await _wait_model_slot()
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
    outcome, validated_at, raw_metadata = None, None, None
    deterministic_repair, repaired_formal_published = None, False
    topic_registry = None
    diagnostics = {}
    model_exit_confirmed = False
    model_operation_entered = False
    identity = research_call_identity(run_id, owner_token)
    lifecycle = ResearchLifecycle(identity)
    execution_state = None
    current_stage = "MODEL_EXECUTION"
    model_started, source_started = None, None
    model_ms, sources_ms = None, None
    execution_receipt, usage_observation = None, None
    execution_transport_reason = None
    execution_statistics, execution_progress = None, None

    def confirmed_model_exit(value):
        nonlocal model_exit_confirmed, execution_state
        safe = sanitize_lifecycle(value, identity)
        if safe is not None and not (model_exit_confirmed and not safe["exitConfirmed"]):
            execution_state = safe
            model_exit_confirmed = model_exit_confirmed or safe["exitConfirmed"]

    def capture_model_result(value):
        nonlocal raw
        raw = deepcopy(value)
        capture_execution_observations(value)

    def capture_execution_observations(value):
        nonlocal execution_statistics, execution_progress
        statistics = sanitize_execution_statistics(getattr(value, "execution_statistics", None))
        progress = sanitize_execution_progress(getattr(value, "execution_progress", None))
        if statistics is not None:
            execution_statistics = statistics
        if progress is not None:
            execution_progress = progress

    async def publish_progress(value):
        nonlocal execution_progress
        safe = sanitize_execution_progress(value)
        if safe is not None:
            execution_progress = safe
            await record_research_progress(identity["runId"], safe)

    async def monitor_local_progress():
        while True:
            await asyncio.sleep(RESEARCH_PROGRESS_SECONDS)
            try:
                await publish_progress(lifecycle.snapshot().get("progress"))
            except Exception as exc:  # noqa: BLE001 -- optional observation only
                logger.warning("daily_research_progress_failed error_class=%s", type(exc).__name__)

    try:
        if unavailable:
            return result
        model = settings.daily_research_model or getattr(settings, "codex_model", None)
        selected_model = model or cli.get("selectedModel")

        async def operation():
            nonlocal model_started, model_ms, model_operation_entered, execution_progress
            # This synchronous boundary precedes every possible CLI/RPC await.
            # If never entered, control flow proves no model was dispatched.
            model_operation_entered = True
            model_started = monotonic()
            timer = asyncio.timeout(deadlines["workerSeconds"])
            progress_task = asyncio.create_task(monitor_local_progress()) if not remote_codex else None
            try:
                async with timer:
                    if remote_codex:
                        value = await codex_client.research(
                            settings, prompt, REPORT_SCHEMA,
                            timeout=settings.daily_research_timeout_seconds,
                            model=selected_model, identity=identity,
                            on_exit=confirmed_model_exit, on_output=capture_model_result,
                            on_progress=publish_progress,
                            trace_usage=False,
                        )
                    else:
                        value = await run_codex_research(
                            cli["executable"], prompt, REPORT_SCHEMA,
                            timeout=settings.daily_research_timeout_seconds,
                            model=selected_model, lifecycle=lifecycle,
                        )
                    capture_model_result(value)
                    confirmed_model_exit(getattr(value, "research_execution_state", None))
                    return value
            except TimeoutError as exc:
                if timer.expired():
                    exc.timeout_origin = "WORKER_OUTER_TIMEOUT"
                raise
            finally:
                if progress_task is not None:
                    progress_task.cancel()
                    await asyncio.gather(progress_task, return_exceptions=True)
                if not remote_codex:
                    snapshot = lifecycle.snapshot()
                    confirmed_model_exit(snapshot)
                    progress = sanitize_execution_progress(snapshot.get("progress"))
                    if progress is not None:
                        # Preserve the last event without making telemetry DB
                        # writes part of model/account completion.
                        execution_progress = progress
                model_ms = max(0, int((monotonic() - model_started) * 1000))

        # Account snapshots have their own bounded budget outside this operation.
        with research_progress_run(identity["runId"]):
            raw = await trace_inference(
                settings, model=selected_model, call_type="research", operation=operation,
            )
        execution_receipt = getattr(raw, "execution_receipt", None)
        execution_transport_reason = getattr(raw, "execution_receipt_transport_reason", None)
        usage_observation = getattr(raw, "usage", None)
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
        sources = sources_for_verification(raw, now=local(now_shanghai()))
        source_started = monotonic()
        checks = await verify_research_sources(sources, now=local(now_shanghai()))
        sources_ms = max(0, int((monotonic() - source_started) * 1000))
        validated_at = local(now_shanghai())
        from backend.macro.research_topics import frozen_manifest

        if manifest := frozen_manifest(packet):
            from backend.macro.research_assurance import bind_execution

            checks = bind_execution(
                checks, execution_receipt, manifest, run_id, validated_at,
                transport_reason=execution_transport_reason,
            )
        write_research_audit(
            settings,
            run_id,
            raw=raw,
            source_checks=checks,
            stage="SOURCES_CHECKED",
            completed_at=local(now_shanghai()),
        )
        current_stage = "REPORT_VALIDATION"
        if (packet.get("researchDiscovery") or {}).get(
            "version"
        ) == "research-discovery-v2":
            from backend.macro.research_batch import evaluate_research_batch

            batch = evaluate_research_batch(
                raw, packet, checks, research_id=str(run_id), validated_at=validated_at
            )
            outcome = batch["originalOutcome"]
            topic_registry = batch["registry"]
            payload = (
                deepcopy(batch["formalReport"]) if batch["autoPublishAllowed"] else None
            )
            if payload is not None:
                payload["researchDiscovery"] = {
                    "version": "research-discovery-v2",
                    "coverage": topic_registry["coverage"],
                    "registryRunId": str(run_id),
                }
                if outcome.formal_payload is None or batch["repairSummary"].get(
                    "changed"
                ):
                    deterministic_repair = batch["repairSummary"]
                    payload["deterministicRepair"] = deterministic_repair
                    repaired_formal_published = True
        else:
            outcome = validate_report_outcome(
                raw,
                packet,
                checks,
                research_id=str(run_id),
                completed_at=validated_at,
                _strict_validator=validate_report,
            )
            payload = outcome.formal_payload
        if payload is None and topic_registry is None:
            repair = repair_report(
                raw,
                packet,
                checks,
                research_id=str(run_id),
                validated_at=validated_at,
            )
            deterministic_repair = repair_summary(repair)
            if repair["strictPass"] and repair["autoPublishAllowed"]:
                payload = deepcopy(repair["formalReport"])
                payload["deterministicRepair"] = deterministic_repair
                repaired_formal_published = True
        if payload is not None and isinstance(packet.get("inputQuality"), dict):
            payload = dict(payload)
            payload["inputQuality"] = packet["inputQuality"]
            payload["sourceBatchId"] = packet.get("sourceBatchId")
        if payload is not None:
            result["status"], error = "READY", None
        else:
            audit_error = outcome.first_error or ResearchValidationError(
                "PAYLOAD_VALIDATION_FAILED"
            )
            error = "ResearchValidationError"
            diagnostics = _log_research_failure(audit_error, current_stage, run_id)
    except asyncio.CancelledError as exc:
        capture_execution_observations(exc)
        confirmed_model_exit(getattr(exc, "research_execution_state", None))
        execution_transport_reason = execution_transport_reason or getattr(exc, "execution_receipt_transport_reason", None)
        execution_receipt = execution_receipt or getattr(exc, "execution_receipt", None)
        usage_observation = usage_observation or getattr(exc, "usage", None)
        audit_error = exc
        result["status"], error = "UNCERTAIN", "CancelledError"
        if pipeline and monotonic() >= pipeline.get("_runtime_deadline", float("inf")):
            error = "PipelineRuntimeExpired"
        diagnostics = _log_research_failure(
            exc,
            current_stage,
            run_id,
            reason_code="PIPELINE_RUNTIME_EXPIRED"
            if error == "PipelineRuntimeExpired"
            else "CANCELLED",
        )
        raise
    except TimeoutError as exc:
        capture_execution_observations(exc)
        confirmed_model_exit(getattr(exc, "research_execution_state", None))
        execution_transport_reason = execution_transport_reason or getattr(exc, "execution_receipt_transport_reason", None)
        execution_receipt = execution_receipt or getattr(exc, "execution_receipt", None)
        usage_observation = usage_observation or getattr(exc, "usage", None)
        audit_error = exc
        result["status"], error = "UNCERTAIN", "TimeoutError"
        diagnostics = _log_research_failure(exc, current_stage, run_id)
    except Exception as exc:  # noqa: BLE001
        capture_execution_observations(exc)
        execution_transport_reason = execution_transport_reason or getattr(exc, "execution_receipt_transport_reason", None)
        execution_receipt = execution_receipt or getattr(exc, "execution_receipt", None)
        usage_observation = usage_observation or getattr(exc, "usage", None)
        audit_error = exc
        confirmed_model_exit(getattr(exc, "research_execution_state", None))
        parse_failure = isinstance(exc, CodexOutputError)
        if parse_failure:
            current_stage = "REPORT_VALIDATION"
            audit_error = ResearchValidationError("SCHEMA_VALIDATION_FAILED")
        captured = getattr(exc, "raw_output", None)
        if captured is not None or getattr(exc, "raw_output_bytes", None) is not None:
            raw = captured
            if raw is None:
                raw_metadata = {
                    "sha256": getattr(exc, "raw_output_sha256", None),
                    "byteLength": getattr(exc, "raw_output_bytes", None),
                    "omissionReason": getattr(exc, "raw_output_omission", "SIZE_LIMIT"),
                }
            validated_at = local(now_shanghai())
            # A failed execution has never completed source verification. Its
            # output is evidence for inspection, never a normal acceptance path.
            outcome = rejected_outcome(
                str(run_id),
                validated_at,
                reason_code=_canonical_reason_code(audit_error),
                rule_id="ACCEPTANCE_PARSE_FAILED"
                if parse_failure
                else "ACCEPTANCE_SYSTEM_FAILURE",
            )
        error = safe_error_class(type(exc).__name__)
        if isinstance(audit_error, ResearchValidationError):
            error = "ResearchValidationError"
        diagnostics = _log_research_failure(audit_error, current_stage, run_id)
    finally:
        from backend.macro.research_workload import measured_workload

        # on_output runs before independent after-account observation; preserve
        # measured inference evidence even if that bookkeeping is interrupted.
        execution_receipt = execution_receipt or getattr(raw, "execution_receipt", None)
        execution_transport_reason = execution_transport_reason or getattr(raw, "execution_receipt_transport_reason", None)
        usage_observation = usage_observation or getattr(raw, "usage", None)
        capture_execution_observations(raw)
        logger.info(
            "daily_research_model_exit run_id=%s exit_confirmed=%s dispatch_started=%s state=%s reason=%s",
            run_id, model_exit_confirmed, model_operation_entered,
            execution_state["state"] if execution_state else "UNKNOWN",
            execution_state.get("terminationReason") if execution_state else "MODEL_EXIT_UNCONFIRMED",
        )
        if model_started is not None and model_ms is None:
            model_ms = max(0, int((monotonic() - model_started) * 1000))
        if source_started is not None and sources_ms is None:
            sources_ms = max(0, int((monotonic() - source_started) * 1000))
        workload = measured_workload(
            len(prompt.encode("utf-8")), execution_receipt, usage_observation,
            model_ms=model_ms, sources_ms=sources_ms,
            transport_reason=execution_transport_reason,
            statistics=execution_statistics, progress=execution_progress,
            model_timing_scope="MODEL_OPERATION", lifecycle=execution_state,
            dispatch_started=model_operation_entered,
        )
        logger.info(
            "daily_research_workload run_id=%s prompt_bytes=%s model_ms=%s source_ms=%s search_queries=%s execution_coverage=%s",
            run_id, workload["promptBytes"], model_ms, sources_ms,
            workload["observedSearchQueries"], workload["executionCoverage"],
        )
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
        try:
            validated_at = validated_at or local(now_shanghai())
            if outcome is None:
                outcome = rejected_outcome(
                    str(run_id),
                    validated_at,
                    reason_code=diagnostics.get("failure_reason_code")
                    or "UNEXPECTED_ERROR",
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
                        **diagnostics,
                    )
                    archived_outcome = outcome.to_dict()
                    archived_outcome["researchWorkload"] = workload
                    if deterministic_repair is not None:
                        archived_outcome["deterministicRepair"] = deterministic_repair
                        archived_outcome["repairedFormalPublished"] = (
                            repaired_formal_published
                        )
                    await archive_repository.enqueue_research_archive(
                        conn,
                        run_id,
                        packet,
                        raw,
                        checks,
                        validated_at,
                        archived_outcome,
                        recorded_at=local(now_shanghai()),
                        **(
                            {"raw_metadata": raw_metadata}
                            if raw_metadata is not None
                            else {}
                        ),
                        **(
                            {"topic_registry": topic_registry}
                            if topic_registry is not None
                            else {}
                        ),
                    )

                async with conn.transaction():
                    if pipeline:
                        from backend.macro.daily_pipeline_repository import (
                            advance_pipeline,
                        )

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
                if pipeline:
                    pipeline["_terminal_written"] = True
        except (Exception, asyncio.CancelledError) as exc:
            if pipeline and not isinstance(
                exc, (PipelineOwnershipLost, asyncio.CancelledError)
            ):
                pipeline["_persistence_failed"] = True
            persist_error = safe_error_class(type(exc).__name__)
            logger.warning(
                "daily_research_persist_failed stage=PERSISTING error_class=%s run_id=%s",
                persist_error,
                run_id,
            )
            _write_diagnostic_summary(
                settings,
                run_id,
                sanitize_failure_diagnostics(
                    stage="PERSISTING",
                    reason_code=(
                        "PIPELINE_RUNTIME_EXPIRED"
                        if pipeline
                        and monotonic()
                        >= pipeline.get("_runtime_deadline", float("inf"))
                        else "CANCELLED"
                    )
                    if isinstance(exc, asyncio.CancelledError)
                    else _canonical_reason_code(exc),
                ),
                persist_error,
                "UNCERTAIN",
            )
            raise
        else:
            _write_diagnostic_summary(
                settings, run_id, diagnostics, error, result["status"]
            )
            # The durable outbox owns archival. Returning here lets the existing
            # confirmed-return guard release finish before any archive work.
            result["auditStatus"] = "AUDIT_PENDING"
        finally:
            if model_exit_confirmed or not model_operation_entered:
                try:
                    async with system_connection(pool) as conn:
                        if not await release_model_guard(
                            conn, run_id, owner_token, local(now_shanghai())
                        ):
                            raise PipelineOwnershipLost()
                except Exception as exc:  # noqa: BLE001 -- retain occupation on failure
                    logger.warning(
                        "daily_research_model_release_failed error_class=%s run_id=%s",
                        safe_error_class(type(exc).__name__),
                        run_id,
                    )
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
    check_research_audit(settings)
    stop_event = stop_event or asyncio.Event()
    current = local(now_provider())
    schedule = _schedule(settings)
    from backend.macro.daily_pipeline_repository import (
        archive_prior_pipelines,
        read_pipeline,
    )

    # Preserve the startup schedule decision even if reconnecting spans its due
    # time. CATCH_UP=false must not turn an on-time service into a late restart.
    while not stop_event.is_set():
        try:
            observed_at = local(now_provider())
            async with _scheduler_database_connection(pool) as conn:
                await archive_prior_pipelines(conn, observed_at)
                existing = await read_pipeline(conn, observed_at)
            break
        except SchedulerDatabaseUnavailable as exc:
            logger.warning(
                "daily_research_database_retry phase=initialize error_class=%s retry_seconds=60",
                exc.error_class,
            )
            await wait(stop_event, 60)
    else:
        return
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
            async with _scheduler_database_connection(pool) as conn:
                prior_pending = await archive_prior_pipelines(conn, current)
        except SchedulerDatabaseUnavailable as exc:
            logger.warning(
                "daily_research_observation_failed error_class=%s retry_seconds=60",
                exc.error_class,
            )
            await wait(stop_event, 60)
            continue
        except Exception as exc:
            logger.error(
                "daily_research_observation_failed error_class=%s retryable=false action=stop_service",
                type(exc).__name__,
            )
            raise
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
        except SchedulerDatabaseUnavailable as exc:
            logger.warning(
                "daily_research_database_retry phase=preclaim error_class=%s retry_seconds=60",
                exc.error_class,
            )
            await wait(stop_event, 60)
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
            0 if result["status"] in {"READY", "SKIPPED", "NOT_DUE", "DISABLED", "COOLDOWN"} else 1
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

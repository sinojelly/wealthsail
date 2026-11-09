"""Append-only global research dispatches; no tenant or trading authority."""

from datetime import datetime, time, timedelta
import json
import re
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
METHOD = "daily-research-v1"
EXECUTION_WINDOW = timedelta(minutes=20)
AUTOMATIC_RESEARCH_COOLDOWN = timedelta(hours=23)


def local(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("daily research requires an aware clock")
    return now.astimezone(SHANGHAI)


def automatic_research_cooldown(last_dispatched_at, now):
    """A dispatch intent may have incurred cost even when its outcome is unknown."""
    stamp = local(now)
    last = local(last_dispatched_at) if last_dispatched_at is not None else None
    eligible = last + AUTOMATIC_RESEARCH_COOLDOWN if last is not None else None
    return {
        "active": eligible is not None and stamp < eligible,
        "hours": 23,
        "lastAiDispatchAt": last,
        "automaticEligibleAt": eligible,
    }


async def read_automatic_research_cooldown(conn, now):
    # Include all modes and terminal outcomes, independently of best-effort usage
    # telemetry. Do not filter by a stale caller clock: a manual dispatch may
    # have committed while the automatic caller was waiting for the global lock.
    last = await conn.fetchval(
        "SELECT max(dispatched_at) FROM research.daily_research_runs"
    )
    return automatic_research_cooldown(last, now)


def scheduled_for(now, schedule_time="07:00"):
    if not isinstance(schedule_time, str) or not re.fullmatch(
        r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", schedule_time
    ):
        raise ValueError("research schedule time must be HH:MM")
    hour, minute = map(int, schedule_time.split(":"))
    return datetime.combine(local(now).date(), time(hour, minute), SHANGHAI)


def is_scheduled_day(now, schedule_mode="DAILY"):
    if schedule_mode not in {"DAILY", "WEEKDAYS"}:
        raise ValueError("unsupported research schedule mode")
    return schedule_mode == "DAILY" or local(now).weekday() < 5


def next_run(now, schedule_mode="DAILY", schedule_time="07:00"):
    slot = scheduled_for(now, schedule_time)
    if local(now) >= slot:
        slot += timedelta(days=1)
    while not is_scheduled_day(slot, schedule_mode):
        slot += timedelta(days=1)
    return slot


def _object(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else None


async def claim_daily_run(
    conn,
    now,
    packet,
    *,
    input_hash,
    prompt_version,
    cli_version=None,
    debug_request_id=None,
    manual_request_id=None,
    source_batch_id=None,
    schedule_time="07:00",
):
    stamp = local(now)
    if debug_request_id is not None and manual_request_id is not None:
        raise ValueError("select one explicit research request mode")
    request_id = (
        manual_request_id if manual_request_id is not None else debug_request_id
    )
    mode = "MANUAL" if manual_request_id is not None else "DEBUG"
    if request_id is not None and source_batch_id is None:
        raise ValueError("explicit research requires its exact source batch")
    slot = stamp if request_id is not None else scheduled_for(stamp, schedule_time)
    if stamp < slot:
        return None
    if request_id is not None:
        return await conn.fetchrow(
            f"""WITH locked_request AS MATERIALIZED (
              SELECT q.* FROM research.daily_research_refresh_requests q
              JOIN identity.tenant_members m ON m.tenant_id=q.tenant_id AND m.user_id=q.user_id
              JOIN identity.tenants t ON t.id=m.tenant_id
              WHERE q.id=$8 AND q.request_mode='{mode}' AND q.requested_at<=$3
                AND m.status='ACTIVE' AND m.role IN ('OWNER','ADMIN') AND t.status='ACTIVE'
              FOR UPDATE OF q)
            INSERT INTO research.daily_research_runs
            (slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet,cli_version,run_mode,debug_request_id,source_batch_id)
            SELECT $1,$2,$3,$4,$5,$6::jsonb,$7,'{mode}',$8,$9
            FROM locked_request q JOIN market.alpha_public_batches b ON b.id=$9
              AND b.debug_request_id=q.id AND b.run_mode='{mode}'
            WHERE q.status='RUNNING' AND q.started_at<=$3 AND q.completed_at IS NULL
              AND GREATEST($3,clock_timestamp())<q.started_at+interval '35 minutes'
              AND b.status IN ('READY','PARTIAL') AND b.claimed_at>=q.started_at
              AND b.claimed_at<=$3 AND b.completed_at<=$3
            ON CONFLICT(debug_request_id) WHERE run_mode='{mode}' DO NOTHING RETURNING *""",
            stamp.date(),
            slot,
            stamp,
            input_hash,
            prompt_version,
            json.dumps(packet, ensure_ascii=False, default=str),
            cli_version,
            request_id,
            source_batch_id,
        )
    # The partial scheduled-slot index is the cross-process cost barrier; never DELETE or
    # UPDATE it on failure. The caller releases this connection before spawn.
    return await conn.fetchrow(
        """INSERT INTO research.daily_research_runs
        (slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet,cli_version,source_batch_id)
        VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8)
        ON CONFLICT(slot_date) WHERE run_mode='SCHEDULED' DO NOTHING RETURNING *""",
        stamp.date(),
        slot,
        stamp,
        input_hash,
        prompt_version,
        json.dumps(packet, ensure_ascii=False, default=str),
        cli_version,
        source_batch_id,
    )


async def consumed(conn, now):
    return await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM research.daily_research_runs WHERE slot_date=$1 AND run_mode='SCHEDULED')",
        local(now).date(),
    )


async def finish_daily_run(
    conn,
    run_id,
    now,
    *,
    status,
    payload=None,
    error_class=None,
    failure_stage=None,
    failure_reason_code=None,
    failure_rule_id=None,
    failure_item_index=None,
    failure_industry_codes=None,
):
    from backend.macro.research_diagnostics import sanitize_failure_diagnostics

    if status not in {"READY", "FAILED", "UNCERTAIN"}:
        raise ValueError("invalid daily terminal status")
    if error_class is not None and not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{0,79}", error_class
    ):
        error_class = "ResearchError"
    if (status == "READY") != isinstance(payload, dict):
        raise ValueError("only READY daily results contain a report")
    diagnostics = sanitize_failure_diagnostics(
        stage=failure_stage if status != "READY" else None,
        reason_code=failure_reason_code if status != "READY" else None,
        rule_id=failure_rule_id if status != "READY" else None,
        item_index=failure_item_index if status != "READY" else None,
        missing_industry_codes=failure_industry_codes if status != "READY" else None,
    )
    await conn.execute(
        """INSERT INTO research.daily_research_results
        (run_id,status,recorded_at,payload,error_class,failure_industry_codes,failure_stage,failure_reason_code,failure_rule_id,failure_item_index)
        SELECT run_id,$2,$3,$4::jsonb,$5,$6::text[],$7,$8,$9,$10 FROM research.daily_research_runs
        WHERE run_id=$1 AND dispatched_at<=$3 ON CONFLICT(run_id) DO NOTHING""",
        run_id,
        status,
        local(now),
        json.dumps(payload, ensure_ascii=False, default=str)
        if payload is not None
        else None,
        error_class,
        diagnostics.get("failure_industry_codes"),
        diagnostics["failure_stage"],
        diagnostics["failure_reason_code"],
        diagnostics["failure_rule_id"],
        diagnostics["failure_item_index"],
    )


_READ = """SELECT r.*,t.status,t.recorded_at,t.payload,t.error_class,
 t.failure_stage,t.failure_reason_code,t.failure_rule_id,t.failure_item_index,t.failure_industry_codes,
 EXISTS(SELECT 1 FROM research.daily_research_runs scheduled
 WHERE scheduled.slot_date=$1 AND scheduled.dispatched_at<=$2 AND scheduled.run_mode='SCHEDULED') AS scheduled_consumed
 FROM research.daily_research_runs r LEFT JOIN research.daily_research_results t
 ON t.run_id=r.run_id AND t.recorded_at<=$2
 WHERE r.slot_date<=$1 AND r.dispatched_at<=$2"""


async def read_daily_research(
    conn,
    now,
    *,
    enabled=True,
    histories=None,
    start_window_minutes=120,
    schedule_mode="DAILY",
    schedule_time="07:00",
    include_failure_industry_codes=False,
):
    from backend.investment.quantitative_scan import report_with_quantitative_scan
    from backend.investment.live_fund_plans import refresh_report_fund_plans
    from backend.common.timing import trace_stage
    from backend.macro.daily_pipeline_repository import read_pipeline
    from backend.macro.research_diagnostics import sanitize_failure_diagnostics
    from backend.macro.research_delivery import read_economic_research
    from backend.macro.research_opportunities import read_fund_observation

    stamp = local(now)
    pipeline = await read_pipeline(conn, stamp)
    latest = await conn.fetchrow(
        _READ + " ORDER BY r.dispatched_at DESC,r.run_id DESC LIMIT 1",
        stamp.date(),
        stamp,
    )
    # A READY latest dispatch is also the newest READY dispatch. Its public
    # packet can be megabytes; do not transfer that same report twice.
    report_row = latest if latest and latest.get("status") == "READY" else None
    if latest and report_row is None:
        report_row = await conn.fetchrow(
            _READ
            + " AND t.status='READY' ORDER BY r.dispatched_at DESC,t.recorded_at DESC,r.run_id DESC LIMIT 1",
            stamp.date(),
            stamp,
        )
    today = latest and latest["slot_date"] == stamp.date()
    manual_latest = bool(latest and latest.get("run_mode") == "MANUAL")
    if (
        pipeline
        and pipeline.get("scheduledFor")
        and local(pipeline["scheduledFor"]).date() < stamp.date()
        and today
    ):
        # Keep today's explicit research result ahead of an older live owner.
        pipeline = None
    if (
        pipeline
        and (
            manual_latest
            or today
            and latest.get("status") == "READY"
            and bool(_object(latest.get("payload")))
        )
        and pipeline.get("startedAt")
        and latest["dispatched_at"] >= local(pipeline["startedAt"])
    ):
        # A later explicit attempt or completed report owns its execution
        # identity. A retained pipeline must not resurrect an older failure.
        pipeline = None
    scheduled_consumed = bool(
        latest
        and latest.get(
            "scheduled_consumed",
            today and latest.get("run_mode", "SCHEDULED") == "SCHEDULED",
        )
    )
    today_slot = scheduled_for(stamp, schedule_time)
    pipeline_today = bool(
        pipeline
        and (
            not pipeline.get("scheduledFor")
            or local(pipeline["scheduledFor"]).date() == stamp.date()
        )
    )
    if pipeline_today and pipeline.get("scheduledFor"):
        today_slot = local(pipeline["scheduledFor"])
    scheduled_day = is_scheduled_day(stamp, schedule_mode)
    window_end = (
        pipeline.get("startWindowEndsAt")
        if pipeline_today
        else today_slot + timedelta(minutes=start_window_minutes)
    )
    schedule_state = (
        "DISABLED"
        if not enabled
        else "DISPATCHED"
        if scheduled_consumed or pipeline_today
        else "NON_WORKDAY"
        if not scheduled_day
        else "NOT_DUE"
        if stamp < today_slot
        else "MISSED"
        if stamp >= window_end
        else "DUE_NOT_DISPATCHED"
    )
    state = "STALE" if latest else "NOT_RUN"
    if today:
        state = latest.get("status") or (
            "UNCERTAIN"
            if stamp - latest["dispatched_at"] > EXECUTION_WINDOW
            else "RUNNING"
        )
    if pipeline:
        # read_pipeline projects the frozen-window/lease/model-slot boundary.
        # Do not reclassify existing work using the current env configuration.
        state = pipeline["state"]
        if state == "MISSED" and enabled and pipeline_today:
            schedule_state = "MISSED"
    elif not today and enabled and scheduled_day and stamp >= window_end:
        state = "MISSED"
    if not enabled and not (today and manual_latest):
        state = "DISABLED"
    packet = _object(report_row.get("public_packet")) if report_row else None
    report = (
        report_with_quantitative_scan(_object(report_row.get("payload")), packet, stamp)
        if report_row
        else None
    )
    if report is not None:
        with trace_stage("context.live_nav_prices"):
            report = await refresh_report_fund_plans(
                conn, report, stamp, histories=histories, packet=packet
            )
            from backend.investment.opportunity_scoring import rank_report

            # Live NAV refresh invalidates saved scores along with saved prices.
            # Recompute with the visible evidence clock and the same cycle guard.
            report = rank_report(report, {**(packet or {}), "asOf": stamp.isoformat()})
    displayed_run_id = (
        pipeline["researchRunId"]
        if pipeline
        else str(latest["run_id"])
        if latest
        else None
    )
    failure_row = (
        latest
        if latest
        and latest.get("status") in {"FAILED", "UNCERTAIN"}
        and displayed_run_id is not None
        and str(latest["run_id"]) == str(displayed_run_id)
        else {}
    )
    diagnostics = sanitize_failure_diagnostics(
        stage=failure_row.get("failure_stage"),
        reason_code=failure_row.get("failure_reason_code"),
        rule_id=failure_row.get("failure_rule_id"),
        item_index=failure_row.get("failure_item_index"),
        # Reports are shared; taxonomy diagnostics need an explicit admin projection.
        missing_industry_codes=failure_row.get("failure_industry_codes")
        if include_failure_industry_codes is True
        else None,
    )
    economic_research = await read_economic_research(conn, displayed_run_id, stamp)
    fund_observation = await read_fund_observation(
        conn, latest, displayed_run_id, stamp
    )
    cooldown = automatic_research_cooldown(
        latest["dispatched_at"] if latest else None, stamp
    )
    next_automatic = next_run(stamp, schedule_mode, schedule_time)
    if (
        enabled
        and cooldown["active"]
        and next_automatic < cooldown["automaticEligibleAt"]
    ):
        next_automatic = next_run(
            cooldown["automaticEligibleAt"] - timedelta(microseconds=1),
            schedule_mode,
            schedule_time,
        )
    return {
        "methodVersion": METHOD,
        "state": state,
        "schedule": {
            "mode": schedule_mode,
            "time": schedule_time,
            "timezone": "Asia/Shanghai",
            "startWindowMinutes": start_window_minutes,
        },
        "scheduleState": schedule_state,
        "todayScheduledFor": (pipeline.get("scheduledFor") or today_slot)
        if pipeline_today
        else today_slot
        if scheduled_day
        else None,
        "pipeline": pipeline,
        "runId": displayed_run_id,
        "failureStage": diagnostics["failure_stage"],
        "failureReasonCode": diagnostics["failure_reason_code"],
        "failureRuleId": diagnostics["failure_rule_id"],
        "failureItemIndex": diagnostics["failure_item_index"],
        "failureIndustryCodes": diagnostics.get("failure_industry_codes"),
        "scheduledFor": pipeline.get("scheduledFor") or today_slot
        if pipeline
        else latest["scheduled_for"]
        if latest
        else today_slot,
        "startedAt": pipeline["startedAt"]
        if pipeline
        else latest["dispatched_at"]
        if latest
        else None,
        "completedAt": pipeline["finishedAt"]
        if pipeline
        else latest.get("recorded_at")
        if latest
        else None,
        "asOf": report_row.get("recorded_at") if report_row else None,
        "reportRunId": str(report_row["run_id"]) if report_row else None,
        "reportCompletedAt": report_row.get("recorded_at") if report_row else None,
        "reportSlotDate": report_row.get("slot_date") if report_row else None,
        "nextRunAt": next_automatic,
        "automaticResearchCooldown": cooldown,
        "errorClass": pipeline["errorClass"]
        if pipeline
        else latest.get("error_class")
        if latest
        else None,
        "runMode": "SCHEDULED"
        if pipeline
        else latest.get("run_mode", "SCHEDULED")
        if latest
        else None,
        "reportRunMode": report_row.get("run_mode", "SCHEDULED")
        if report_row
        else None,
        "sourceBatchId": str(report_row["source_batch_id"])
        if report_row and report_row.get("source_batch_id")
        else None,
        "promptVersion": report_row.get("prompt_version") if report_row else None,
        "report": report,
        "economicResearch": economic_research,
        "fundObservation": fund_observation,
    }


async def read_recent_public_research(conn, now):
    """Bounded READY public reports only; never query actor study records."""
    stamp = local(now)
    rows = await conn.fetch(
        "WITH visible AS ("
        + _READ
        + " AND t.status='READY' AND t.recorded_at>=$3), ranked AS ("
        + "SELECT *,row_number() OVER(PARTITION BY slot_date ORDER BY (run_mode='SCHEDULED') DESC,recorded_at DESC,run_id) AS day_rank FROM visible) "
        + "SELECT * FROM ranked WHERE day_rank=1 OR run_id IN (SELECT run_id FROM visible ORDER BY recorded_at DESC,run_id LIMIT 14) "
        + "ORDER BY recorded_at DESC,run_id LIMIT 30",
        stamp.date(),
        stamp,
        stamp - timedelta(days=14),
    )
    return [
        {
            "status": "READY",
            "runId": str(row["run_id"]),
            "completedAt": row["recorded_at"],
            "slotDate": row["slot_date"],
            "report": _object(row.get("payload")),
        }
        for row in rows
    ]


async def read_previous_daily_research(conn, now):
    """A price-comparison baseline must predate this Shanghai calendar slot."""
    stamp = local(now)
    row = await conn.fetchrow(
        """SELECT r.run_id,r.slot_date,r.dispatched_at,t.recorded_at,t.payload
        FROM research.daily_research_runs r JOIN research.daily_research_results t
        ON t.run_id=r.run_id
        WHERE r.slot_date<$1 AND t.status='READY'
          AND r.dispatched_at<=$2 AND t.recorded_at<=$2
        ORDER BY r.slot_date DESC,r.dispatched_at DESC,t.recorded_at DESC,r.run_id DESC
        LIMIT 1""",
        stamp.date(),
        stamp,
    )
    return {
        "reportRunId": str(row["run_id"]) if row else None,
        "reportSlotDate": row["slot_date"] if row else None,
        "reportCompletedAt": row["recorded_at"] if row else None,
        "report": _object(row.get("payload")) if row else None,
    }

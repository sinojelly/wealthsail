"""Append-only global research dispatches; no tenant or trading authority."""

from datetime import datetime, time, timedelta
import json
import re
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
METHOD = "daily-research-v1"
EXECUTION_WINDOW = timedelta(minutes=20)


def local(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("daily research requires an aware clock")
    return now.astimezone(SHANGHAI)


def scheduled_for(now):
    return datetime.combine(local(now).date(), time(8, 30), SHANGHAI)


def next_run(now):
    slot = scheduled_for(now)
    return slot if local(now) < slot else slot + timedelta(days=1)


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
    source_batch_id=None,
):
    stamp = local(now)
    slot = stamp if debug_request_id is not None else scheduled_for(stamp)
    if stamp < slot:
        return None
    if debug_request_id is not None:
        return await conn.fetchrow(
            """INSERT INTO research.daily_research_runs
            (slot_date,scheduled_for,dispatched_at,input_hash,prompt_version,public_packet,cli_version,run_mode,debug_request_id,source_batch_id)
            VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,'DEBUG',$8,$9)
            ON CONFLICT(debug_request_id) WHERE run_mode='DEBUG' DO NOTHING RETURNING *""",
            stamp.date(),
            slot,
            stamp,
            input_hash,
            prompt_version,
            json.dumps(packet, ensure_ascii=False, default=str),
            cli_version,
            debug_request_id,
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
    conn, run_id, now, *, status, payload=None, error_class=None
):
    if status not in {"READY", "FAILED", "UNCERTAIN"}:
        raise ValueError("invalid daily terminal status")
    if error_class is not None and not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{0,79}", error_class
    ):
        error_class = "ResearchError"
    if (status == "READY") != isinstance(payload, dict):
        raise ValueError("only READY daily results contain a report")
    await conn.execute(
        """INSERT INTO research.daily_research_results(run_id,status,recorded_at,payload,error_class)
        SELECT run_id,$2,$3,$4::jsonb,$5 FROM research.daily_research_runs
        WHERE run_id=$1 AND dispatched_at<=$3 ON CONFLICT(run_id) DO NOTHING""",
        run_id,
        status,
        local(now),
        json.dumps(payload, ensure_ascii=False, default=str)
        if payload is not None
        else None,
        error_class,
    )


_READ = """SELECT r.*,t.status,t.recorded_at,t.payload,t.error_class,
 EXISTS(SELECT 1 FROM research.daily_research_runs scheduled
 WHERE scheduled.slot_date=$1 AND scheduled.dispatched_at<=$2 AND scheduled.run_mode='SCHEDULED') AS scheduled_consumed
 FROM research.daily_research_runs r LEFT JOIN research.daily_research_results t
 ON t.run_id=r.run_id AND t.recorded_at<=$2
 WHERE r.slot_date<=$1 AND r.dispatched_at<=$2"""


async def read_daily_research(conn, now, *, enabled=True):
    stamp = local(now)
    latest = await conn.fetchrow(
        _READ + " ORDER BY r.dispatched_at DESC,r.run_id DESC LIMIT 1",
        stamp.date(),
        stamp,
    )
    previous = await conn.fetchrow(
        _READ
        + " AND t.status='READY' ORDER BY r.dispatched_at DESC,t.recorded_at DESC,r.run_id DESC LIMIT 1",
        stamp.date(),
        stamp,
    )
    report_row = latest if latest and latest.get("status") == "READY" else previous
    today = latest and latest["slot_date"] == stamp.date()
    scheduled_consumed = bool(
        latest
        and latest.get(
            "scheduled_consumed",
            today and latest.get("run_mode", "SCHEDULED") == "SCHEDULED",
        )
    )
    today_slot = scheduled_for(stamp)
    schedule_state = (
        "DISABLED"
        if not enabled
        else "DISPATCHED"
        if scheduled_consumed
        else "NOT_DUE"
        if stamp < today_slot
        else "DUE_NOT_DISPATCHED"
    )
    state = "STALE" if latest else "NOT_RUN"
    if today:
        state = latest.get("status") or (
            "UNCERTAIN"
            if stamp - latest["dispatched_at"] > EXECUTION_WINDOW
            else "RUNNING"
        )
    if not enabled:
        state = "DISABLED"
    return {
        "methodVersion": METHOD,
        "state": state,
        "scheduleState": schedule_state,
        "todayScheduledFor": today_slot,
        "runId": str(latest["run_id"]) if latest else None,
        "scheduledFor": latest["scheduled_for"] if latest else scheduled_for(stamp),
        "startedAt": latest["dispatched_at"] if latest else None,
        "completedAt": latest.get("recorded_at") if latest else None,
        "asOf": report_row.get("recorded_at") if report_row else None,
        "reportRunId": str(report_row["run_id"]) if report_row else None,
        "reportCompletedAt": report_row.get("recorded_at") if report_row else None,
        "reportSlotDate": report_row.get("slot_date") if report_row else None,
        "nextRunAt": next_run(stamp),
        "errorClass": latest.get("error_class") if latest else None,
        "runMode": latest.get("run_mode", "SCHEDULED") if latest else None,
        "reportRunMode": report_row.get("run_mode", "SCHEDULED")
        if report_row
        else None,
        "sourceBatchId": str(report_row["source_batch_id"])
        if report_row and report_row.get("source_batch_id")
        else None,
        "promptVersion": report_row.get("prompt_version") if report_row else None,
        "report": _object(report_row.get("payload")) if report_row else None,
    }


async def read_recent_public_research(conn, now):
    """Bounded READY public reports only; never query actor study records."""
    stamp = local(now)
    rows = await conn.fetch(
        _READ
        + " AND t.status='READY' AND t.recorded_at>=$3"
        + " ORDER BY t.recorded_at DESC,r.run_id LIMIT 14",
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

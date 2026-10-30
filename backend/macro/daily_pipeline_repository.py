"""Durable single-owner scheduled collection and research pipeline."""

from datetime import timedelta
import json
import re
from uuid import UUID, uuid4
from urllib.parse import urlsplit

from backend.macro.daily_repository import local, scheduled_for
from backend.macro.market_repository import _day, _stamp

LEASE = timedelta(minutes=30)
STAGES = {
    "COLLECTING",
    "WAITING_SOURCES",
    "VALIDATING_INPUT",
    "MODEL_RUNNING",
    "VERIFYING_OUTPUT",
    "PERSISTING",
}
TERMINAL = {"READY", "FAILED", "UNCERTAIN", "MISSED"}
PRE_MODEL_STAGES = {"COLLECTING", "WAITING_SOURCES", "VALIDATING_INPUT"}


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def dto(row, now):
    row = _decode(row)
    if not isinstance(row, dict):
        return None
    stamp = local(now)
    expired = row["state"] == "RUNNING" and _stamp(row["lease_until"]) <= stamp
    pre_model = (
        row["stage"] in PRE_MODEL_STAGES
        and not row.get("research_run_id")
        and not row.get("scheduled_model_consumed")
    )
    window_end = _stamp(row.get("start_window_ends_at"))
    missed = expired and pre_model and window_end is not None and window_end <= stamp
    uncertain = (
        expired and not missed and (row["stage"] != "WAITING_SOURCES" or not pre_model)
    )
    return {
        "id": str(row["id"]),
        "state": "MISSED" if missed else "UNCERTAIN" if uncertain else row["state"],
        "stage": row["stage"],
        "scheduledFor": _stamp(row.get("scheduled_for")),
        "startWindowEndsAt": window_end,
        "runtimeDeadlineAt": _stamp(row.get("runtime_deadline_at")),
        "startedAt": _stamp(row["started_at"]),
        "updatedAt": _stamp(row["updated_at"]),
        "finishedAt": _stamp(row.get("finished_at")),
        "sourceBatchId": str(row["source_batch_id"])
        if row.get("source_batch_id")
        else None,
        "researchRunId": str(row["research_run_id"])
        if row.get("research_run_id")
        else None,
        "errorClass": "SourceWaitWindowExpired"
        if missed
        else "PipelineLeaseExpired"
        if uncertain
        else row.get("error_class"),
        "quality": _decode(row.get("quality")),
    }


async def read_pipeline(conn, now):
    row = await conn.fetchval(
        """SELECT to_jsonb(p) || jsonb_build_object('scheduled_model_consumed',
          EXISTS(SELECT 1 FROM research.daily_research_runs r
            WHERE r.slot_date=p.slot_date AND r.run_mode='SCHEDULED'))
        FROM research.daily_research_pipelines p WHERE slot_date=$1 AND started_at<=$2""",
        local(now).date(),
        local(now),
    )
    return dto(row, now)


async def claim_pipeline(
    conn,
    now,
    *,
    schedule_time="07:00",
    start_window_minutes=120,
    pipeline_timeout_seconds=7200,
):
    stamp, token = local(now), uuid4()
    due = scheduled_for(stamp, schedule_time=schedule_time)
    # Only pre-model owners can be replaced. The immutable scheduled model slot
    # is checked separately from the cached FK: a crash between callers must
    # never turn a consumed model slot into permission to dispatch again.
    # A recovered owner keeps both frozen deadlines. Even when runtime has run
    # out it may take ownership inside the start window solely to archive FAILED;
    # the job checks runtime before collecting sources or dispatching a model.
    await conn.execute(
        """UPDATE research.daily_research_pipelines SET state='UNCERTAIN',
        error_class='PipelineLeaseExpired',finished_at=$2,updated_at=$2
        WHERE slot_date=$1 AND state='RUNNING' AND lease_until<$2
          AND (stage IN ('MODEL_RUNNING','VERIFYING_OUTPUT','PERSISTING')
            OR research_run_id IS NOT NULL
            OR EXISTS(SELECT 1 FROM research.daily_research_runs r
              WHERE r.slot_date=$1 AND r.run_mode='SCHEDULED'))""",
        stamp.date(),
        stamp,
    )
    return await conn.fetchrow(
        """INSERT INTO research.daily_research_pipelines
        (slot_date,scheduled_for,started_at,updated_at,state,stage,lease_token,lease_until,
          start_window_ends_at,runtime_deadline_at)
        SELECT $1,$2,$3,$3,'RUNNING','COLLECTING',$4,$5,$6,$7
        WHERE NOT EXISTS(SELECT 1 FROM research.daily_research_runs r
          WHERE r.slot_date=$1 AND r.run_mode='SCHEDULED')
          AND (($3::timestamptz>=$2::timestamptz AND $3::timestamptz<$6::timestamptz) OR EXISTS(
            SELECT 1 FROM research.daily_research_pipelines p WHERE p.slot_date=$1))
        ON CONFLICT(slot_date) DO UPDATE SET lease_token=$4,lease_until=$5,updated_at=$3,
          stage='COLLECTING',error_class=NULL
        WHERE research.daily_research_pipelines.state='RUNNING'
          AND research.daily_research_pipelines.stage IN ('COLLECTING','WAITING_SOURCES','VALIDATING_INPUT')
          AND research.daily_research_pipelines.lease_until<=$3
          AND research.daily_research_pipelines.start_window_ends_at>$3
          AND research.daily_research_pipelines.research_run_id IS NULL
          AND NOT EXISTS(SELECT 1 FROM research.daily_research_runs r
            WHERE r.slot_date=$1 AND r.run_mode='SCHEDULED')
        RETURNING *""",
        stamp.date(),
        due,
        stamp,
        token,
        stamp + LEASE,
        due + timedelta(minutes=start_window_minutes),
        stamp + timedelta(seconds=pipeline_timeout_seconds),
    )


async def renew_pipeline_lease(conn, claim, now):
    """A heartbeat may extend only its own still-live RUNNING lease."""
    stamp = local(now)
    return (
        await conn.fetchval(
            """UPDATE research.daily_research_pipelines SET lease_until=$4,updated_at=$3
        WHERE id=$1 AND lease_token=$2 AND state='RUNNING' AND lease_until>=$3
          AND stage<>'WAITING_SOURCES' RETURNING id""",
            claim["id"],
            claim["lease_token"],
            stamp,
            stamp + LEASE,
        )
        is not None
    )


async def release_pipeline_before_model(conn, claim, now):
    """Normal shutdown releases pre-model work; source batches remain immutable."""
    stamp = local(now)
    return (
        await conn.fetchval(
            """UPDATE research.daily_research_pipelines SET stage='WAITING_SOURCES',
          lease_until=$3,updated_at=$3,error_class='WorkerStoppedBeforeModel'
        WHERE id=$1 AND lease_token=$2 AND state='RUNNING' AND lease_until>=$3
          AND stage IN ('COLLECTING','WAITING_SOURCES','VALIDATING_INPUT')
          AND research_run_id IS NULL AND NOT EXISTS(
            SELECT 1 FROM research.daily_research_runs r
            WHERE r.slot_date=research.daily_research_pipelines.slot_date
              AND r.run_mode='SCHEDULED') RETURNING id""",
            claim["id"],
            claim["lease_token"],
            stamp,
        )
        is not None
    )


async def advance_pipeline(
    conn,
    claim,
    now,
    *,
    stage,
    state="RUNNING",
    source_batch_id=None,
    research_run_id=None,
    quality=None,
    error_class=None,
):
    if stage not in STAGES or state not in TERMINAL | {"RUNNING"}:
        raise ValueError("invalid pipeline transition")
    if error_class and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_class):
        error_class = "PipelineError"
    stamp = local(now)
    row = await conn.fetchrow(
        """UPDATE research.daily_research_pipelines SET stage=$3,state=$4,updated_at=$5,
        finished_at=CASE WHEN $4='RUNNING' THEN NULL ELSE $5 END,
        lease_until=CASE WHEN $3='WAITING_SOURCES' THEN $5 ELSE $6 END,
        source_batch_id=COALESCE($7,source_batch_id),research_run_id=COALESCE($8,research_run_id),
        quality=COALESCE($9::jsonb,quality),error_class=$10
        WHERE id=$1 AND lease_token=$2 AND state='RUNNING' AND lease_until>=$5
        RETURNING id""",
        claim["id"],
        claim["lease_token"],
        stage,
        state,
        stamp,
        stamp + LEASE,
        UUID(str(source_batch_id)) if source_batch_id else None,
        UUID(str(research_run_id)) if research_run_id else None,
        json.dumps(quality, allow_nan=False) if quality is not None else None,
        error_class,
    )
    return row is not None


async def scheduled_source_batch(conn, now):
    return await conn.fetchrow(
        """SELECT id,run_date,claimed_at,completed_at,status FROM market.alpha_public_batches
        WHERE run_date=$1 AND run_mode='SCHEDULED' AND claimed_at<=$2""",
        local(now).date(),
        local(now),
    )


async def finish_source_wait_window(conn, now):
    """Close a pre-model wait; never interrupt an already dispatched model."""
    stamp = local(now)
    await conn.execute(
        """UPDATE research.daily_research_pipelines SET state='MISSED',
        error_class='SourceWaitWindowExpired',finished_at=$2,updated_at=$2
        WHERE slot_date=$1 AND state='RUNNING'
          AND stage IN ('COLLECTING','WAITING_SOURCES','VALIDATING_INPUT')
          AND start_window_ends_at<=$2 AND lease_until<=$2
          AND research_run_id IS NULL AND NOT EXISTS(
            SELECT 1 FROM research.daily_research_runs r
            WHERE r.slot_date=$1 AND r.run_mode='SCHEDULED')""",
        stamp.date(),
        stamp,
    )


def _source_uri(value):
    try:
        uri = urlsplit(value or "")
        return (
            uri.scheme == "https"
            and bool(uri.hostname)
            and not uri.username
            and not uri.password
        )
    except ValueError:
        return False


def source_quality(bundle, now):
    """Require real public facts plus at least one reliably sourced NAV history.

    Monthly reference dates remain monthly; their availability/retrieval clocks
    must be real and not in the future. No all-empty PARTIAL batch can pass.
    """
    from backend.macro.alpha_repository import LABELS, _point
    from backend.funds.nav_history import (
        valid_points,
        full_history_proven,
        recently_checked,
    )
    from backend.investment.theme_graph import _history_quality
    from backend.funds.nav_window import two_year_nav_points

    stamp = local(now)
    macros = sum(
        _point(row, LABELS[row["code"]], stamp, bundle)["state"] == "OBSERVED"
        for row in bundle.get("observations", [])
        if isinstance(row, dict) and row.get("code") in LABELS
    )
    catalysts = sum(
        bool(row.get("title"))
        and _source_uri(row.get("sourceUri"))
        and (bundle.get("sourceResults", {}).get(row.get("sourceKey")) or {}).get(
            "status"
        )
        in {"SUCCESS", "PARTIAL"}
        and _stamp(row.get("retrievedAt")) is not None
        and _stamp(row["retrievedAt"]) <= stamp
        and (
            row.get("availableAt") is None
            or _stamp(row.get("availableAt")) is not None
            and _stamp(row["availableAt"]) <= stamp
        )
        and _stamp(row.get("publishedAt")) is not None
        and stamp - timedelta(days=7) <= _stamp(row["publishedAt"]) <= stamp
        for row in bundle.get("catalysts", [])
        if isinstance(row, dict)
    )
    reliable = 0
    for history in bundle.get("fundHistories", []):
        if not isinstance(history, dict):
            continue
        if (
            not isinstance(history.get("fundCode"), str)
            or not re.fullmatch(r"[0-9]{6}", history["fundCode"])
            or history.get("currency", "CNY") != "CNY"
            or history.get("market") not in {None, "FUND_CN"}
            or history.get("unit") not in {None, "CNY", "CNY/份", "元/份", "CNY/unit"}
        ):
            continue
        points = valid_points(history, history.get("fundCode"), stamp)
        if (
            len(two_year_nav_points(points)) >= 120
            and recently_checked(history, stamp)
            and history.get("asOf") == points[-1]["date"]
            and _stamp(history["retrievedAt"]).date() >= _day(points[-1]["date"])
            and _day(points[-1]["date"]) >= stamp.date() - timedelta(days=7)
            and (
                full_history_proven(history, points, stamp)
                or _history_quality(history, points, stamp)[0] == "READY"
            )
        ):
            reliable += 1
    failed = sorted(
        key
        for key, result in {
            **bundle.get("sourceResults", {}),
            **bundle.get("themeNavSourceResults", {}),
        }.items()
        if isinstance(result, dict)
        and result.get("status") not in {"SUCCESS", "SKIPPED"}
    )
    reason = (
        "NO_VALID_PUBLIC_FACTS"
        if not (macros or catalysts)
        else "NO_RELIABLE_FUND_NAV"
        if not reliable
        else "VALID_PARTIAL_INPUT"
        if bundle.get("collectionState") == "PARTIAL"
        else "VALID_INPUT"
    )
    allowed = bool(
        (macros or catalysts)
        and reliable
        and bundle.get("collectionState") in {"READY", "PARTIAL"}
    )
    return {
        "allowed": allowed,
        "status": bundle.get("collectionState") if allowed else "BLOCKED",
        "reason": reason,
        "macroCount": macros,
        "catalystCount": catalysts,
        "reliableNavCount": reliable,
        "failedSources": failed,
    }

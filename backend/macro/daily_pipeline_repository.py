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


def _decode(value):
    return json.loads(value) if isinstance(value, str) else value


def dto(row, now):
    row = _decode(row)
    if not isinstance(row, dict):
        return None
    expired = (
        row["state"] == "RUNNING"
        and row["stage"] != "WAITING_SOURCES"
        and _stamp(row["lease_until"]) < local(now)
    )
    return {
        "id": str(row["id"]),
        "state": "UNCERTAIN" if expired else row["state"],
        "stage": row["stage"],
        "startedAt": _stamp(row["started_at"]),
        "updatedAt": _stamp(row["updated_at"]),
        "finishedAt": _stamp(row.get("finished_at")),
        "sourceBatchId": str(row["source_batch_id"])
        if row.get("source_batch_id")
        else None,
        "researchRunId": str(row["research_run_id"])
        if row.get("research_run_id")
        else None,
        "errorClass": "PipelineLeaseExpired" if expired else row.get("error_class"),
        "quality": _decode(row.get("quality")),
    }


async def read_pipeline(conn, now):
    row = await conn.fetchval(
        "SELECT to_jsonb(p) FROM research.daily_research_pipelines p WHERE slot_date=$1 AND started_at<=$2",
        local(now).date(),
        local(now),
    )
    return dto(row, now)


async def claim_pipeline(conn, now):
    stamp, token = local(now), uuid4()
    # An expired owner cannot be silently replaced while it might still dispatch
    # a model. Only the explicit waiting state can be resumed without ambiguity.
    await conn.execute(
        """UPDATE research.daily_research_pipelines SET state='UNCERTAIN',
        error_class='PipelineLeaseExpired',finished_at=$2,updated_at=$2
        WHERE slot_date=$1 AND state='RUNNING' AND stage<>'WAITING_SOURCES' AND lease_until<$2""",
        stamp.date(),
        stamp,
    )
    return await conn.fetchrow(
        """INSERT INTO research.daily_research_pipelines
        (slot_date,scheduled_for,started_at,updated_at,state,stage,lease_token,lease_until)
        VALUES($1,$2,$3,$3,'RUNNING','COLLECTING',$4,$5)
        ON CONFLICT(slot_date) DO UPDATE SET lease_token=$4,lease_until=$5,updated_at=$3
        WHERE research.daily_research_pipelines.state='RUNNING'
          AND research.daily_research_pipelines.stage='WAITING_SOURCES'
          AND research.daily_research_pipelines.lease_until<=$3
          AND research.daily_research_pipelines.research_run_id IS NULL
        RETURNING *""",
        stamp.date(),
        scheduled_for(stamp),
        stamp,
        token,
        stamp + LEASE,
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
        WHERE slot_date=$1 AND state='RUNNING' AND stage='WAITING_SOURCES'
          AND research_run_id IS NULL""",
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

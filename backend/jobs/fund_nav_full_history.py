"""Independent full disclosed-NAV chart cohorts; no changes to research tails."""

import asyncio
from contextlib import redirect_stdout
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import UUID, uuid4

from backend.common.database import system_connection
from backend.dependencies import now_shanghai
from backend.errors import DataUnavailableError
from backend.funds.nav_history import (
    MAX_POINTS,
    full_history_proven,
    local,
    obj,
    recently_checked,
    valid_points,
)
from backend.integrations.nav_history_proof import nav_points_digest
from backend.macro.research_debug_repository import active_operator


def validate_full_payload(payload, code, now):
    now = local(now)
    source = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    if (
        not isinstance(payload, dict)
        or payload.get("fundCode") != code
        or payload.get("sourceUri") != source
        or payload.get("queryStart") != "1900-01-01"
    ):
        raise ValueError("full NAV identity/provenance mismatch")
    end = date.fromisoformat(payload["queryEnd"])
    raw = payload.get("points")
    if (
        end > now.date()
        or type(payload.get("complete")) is not bool
        or not isinstance(raw, list)
        or not 0 < len(raw) <= MAX_POINTS
        or type(payload.get("receivedRowCount")) is not int
        or payload["receivedRowCount"] != len(raw)
    ):
        raise ValueError("full NAV response incomplete or outside its bound")
    by_day = {}
    for point in raw:
        try:
            day = date.fromisoformat(point["date"])
            if isinstance(point["close"], bool):
                raise ValueError("invalid unit NAV")
            value = Decimal(str(point["close"]))
            if (
                not date(1900, 1, 1) <= day <= end
                or not value.is_finite()
                or value <= 0
                or day in by_day
                and by_day[day] != value
            ):
                raise ValueError("invalid or conflicting full unit NAV")
            by_day[day] = value
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise ValueError("invalid full unit NAV coordinate") from exc
    points = [
        {"date": day.isoformat(), "close": str(value)}
        for day, value in sorted(by_day.items())
    ]
    proof = {
        "methodVersion": "akshare-unit-nav-full-v1",
        "fundCode": code,
        "sourceUri": source,
        "queryStart": "1900-01-01",
        "queryEnd": end.isoformat(),
        "proofScope": "PROVIDER_UNIT_NAV_SERIES",
        "complete": payload["complete"],
        "receivedRowCount": len(raw),
        "pointCount": len(points),
        "firstDate": points[0]["date"],
        "lastDate": points[-1]["date"],
        "pointsDigest": nav_points_digest(points),
    }
    return {
        "fundCode": code,
        "priceBasis": "UNIT_NAV",
        "sourceUri": source,
        "retrievedAt": now.isoformat(),
        "availableAt": now.isoformat(),
        "asOf": points[-1]["date"],
        "points": points,
        "fullHistory": payload["complete"],
        "historyProof": proof,
    }


def _fetch_payload(code, end):
    import akshare as ak

    frame = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    if (
        not {"净值日期", "单位净值"} <= set(frame.columns)
        or not 0 < len(frame) <= MAX_POINTS
    ):
        raise ValueError("full unit NAV source columns or point bound changed")
    points = []
    for row in frame.to_dict("records"):
        raw_day = row["净值日期"]
        day = (
            raw_day.date()
            if hasattr(raw_day, "date")
            else date.fromisoformat(str(raw_day))
        )
        points.append({"date": day.isoformat(), "close": str(row["单位净值"])})
    return {
        "fundCode": code,
        "sourceUri": f"https://fund.eastmoney.com/pingzhongdata/{code}.js",
        "queryStart": "1900-01-01",
        "queryEnd": end.isoformat(),
        "complete": True,
        "receivedRowCount": len(points),
        "points": points,
    }


async def fetch_full_payload(code, end):
    # A provider process receives no DB, model or webhook credentials.
    allowed = {
        "path",
        "systemroot",
        "windir",
        "temp",
        "tmp",
        "appdata",
        "localappdata",
        "userprofile",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "ssl_cert_file",
        "ssl_cert_dir",
    }
    environment = {
        key: value for key, value in os.environ.items() if key.lower() in allowed
    }
    environment.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "backend.jobs.fund_nav_full_history",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    try:
        output, _ = await asyncio.wait_for(
            process.communicate(
                json.dumps({"fundCode": code, "end": end.isoformat()}).encode()
            ),
            timeout=60,
        )
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode != 0 or len(output) > 2_000_000:
        raise ValueError("full unit NAV supplier unavailable or response too large")
    return json.loads(output)


async def _claim(pool, code, now):
    key = f"FUND_NAV_CHART:{code}"
    async with system_connection(pool) as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
            rows = await conn.fetch(
                """SELECT payload FROM market.fund_nav_chart_histories WHERE fund_code=$1 AND retrieved_at<=$2 AND available_at<=$2 ORDER BY retrieved_at DESC,id DESC LIMIT 8""",
                code,
                now,
            )
            recent = await conn.fetch(
                """SELECT payload FROM market.fund_research_nav_histories WHERE fund_code=$1 AND retrieved_at<=$2 AND available_at<=$2 ORDER BY retrieved_at DESC,id DESC LIMIT 8""",
                code,
                now,
            )
            known_dates = [
                points[-1]["date"]
                for row in recent
                if (points := valid_points(obj(row["payload"]), code, now))
            ]
            latest_known = max(known_dates, default="")
            for row in rows:
                history = obj(row["payload"])
                points = valid_points(history, code, now)
                if (
                    full_history_proven(history, points, now)
                    and recently_checked(history, now)
                    and points[-1]["date"] >= latest_known
                ):
                    return {"status": "CACHED", "history": history}
            active = await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM market.macro_evidence_attempts c WHERE c.job_key=$1 AND c.phase='CLAIMED' AND c.lease_until>$2 AND NOT EXISTS(SELECT 1 FROM market.macro_evidence_attempts f WHERE f.run_id=c.run_id AND f.phase IN ('FINISHED','CANCELLED') AND f.recorded_at<=$2))""",
                key,
                now,
            )
            if active:
                return {"status": "BUSY"}
            recent = await conn.fetchrow(
                """SELECT result FROM market.macro_evidence_attempts WHERE job_key=$1 AND phase IN ('FINISHED','CANCELLED') AND recorded_at BETWEEN $2::timestamptz-interval '1 hour' AND $2::timestamptz ORDER BY recorded_at DESC,id DESC LIMIT 1""",
                key,
                now,
            )
            if recent and obj(recent["result"]).get("status") != "SUCCESS":
                return {"status": "COOLDOWN"}
            identity = uuid4()
            await conn.execute(
                """INSERT INTO market.macro_evidence_attempts(run_id,job_key,phase,started_at,recorded_at,lease_until,trigger,release_keys) VALUES($1,$2,'CLAIMED',$3,$3,$4,'NAV_CHART','[]'::jsonb)""",
                identity,
                key,
                now,
                now + timedelta(minutes=3),
            )
            return {
                "status": "CLAIMED",
                "runId": identity,
                "key": key,
                "startedAt": now,
                "latestKnownNavDate": latest_known,
            }


async def _finish(pool, claim, now, result, cancelled):
    async with system_connection(pool) as conn:
        await conn.execute(
            """INSERT INTO market.macro_evidence_attempts(run_id,job_key,phase,started_at,recorded_at,trigger,release_keys,result) VALUES($1,$2,$3,$4,$5,'NAV_CHART','[]'::jsonb,$6::jsonb) ON CONFLICT DO NOTHING""",
            claim["runId"],
            claim["key"],
            "CANCELLED" if cancelled else "FINISHED",
            claim["startedAt"],
            now,
            json.dumps(result),
        )


async def refresh_full_nav_history(
    pool, code, *, now=None, fetcher=None, now_provider=None
):
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        raise ValueError("full NAV requires a six-digit code")
    now = local(now or now_shanghai())
    clock = now_provider or now_shanghai
    claim = await _claim(pool, code, now)
    if claim["status"] == "CACHED":
        return claim["history"]
    if claim["status"] == "BUSY":
        return None
    if claim["status"] != "CLAIMED":
        raise DataUnavailableError("单位净值来源暂处冷却期")
    result, cancelled = {"status": "FAILED"}, False
    retrieved = None
    try:
        payload = await asyncio.wait_for(
            (fetcher or fetch_full_payload)(code, now.date()), timeout=60
        )
        finished = local(clock())
        if finished < now:
            raise ValueError("full NAV clock regressed")
        retrieved = finished
        history = validate_full_payload(payload, code, finished)
        if history["asOf"] < claim.get("latestKnownNavDate", ""):
            history["fullHistory"] = False
            history["coverageReason"] = "SOURCE_BEHIND_CONFIRMED_NAV"
        async with system_connection(pool) as conn:
            await conn.execute(
                """INSERT INTO market.fund_nav_chart_histories(fund_code,retrieved_at,available_at,payload) VALUES($1,$2,$2,$3::jsonb)""",
                code,
                finished,
                json.dumps(history),
            )
        result.update(
            status="SUCCESS" if history["fullHistory"] else "PARTIAL",
            records=len(history["points"]),
        )
        return history
    except asyncio.CancelledError:
        cancelled = True
        result["errorClass"] = "CancelledError"
        raise
    except Exception as exc:
        result["errorClass"] = type(exc).__name__
        raise DataUnavailableError("全历史单位净值暂未补齐，保留已有历史") from None
    finally:
        finished = local(clock())
        if finished < now or retrieved is not None and finished < retrieved:
            result.update(status="FAILED", errorClass="ClockRegression")
        if finished >= now:
            cleanup = asyncio.create_task(
                _finish(pool, claim, finished, result, cancelled)
            )
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise


async def process_full_history_task(pool, lease):
    code = lease.payload.get("fundCode")
    try:
        actor = UUID(lease.payload["requestedBy"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("NAV history request has no operator identity") from exc
    async with system_connection(pool) as conn:
        if not await active_operator(conn, lease.tenant_id, actor):
            raise ValueError("NAV history request operator no longer authorized")
        identities = await conn.fetch(
            """SELECT currency,fund_name FROM reference.research_fund_catalog WHERE market='FUND_CN' AND trade_form IN ('FUND','LOF') AND fund_code=$1""",
            code,
        )
    if (
        len(identities) != 1
        or identities[0]["currency"] != "CNY"
        or re.search(r"美元|港币|港元|外币|现汇|现钞", identities[0]["fund_name"])
    ):
        raise ValueError("NAV history request identity no longer unique")
    await refresh_full_nav_history(pool, code)


def main():
    request = json.loads(sys.stdin.buffer.read(4096))
    code, end = request["fundCode"], date.fromisoformat(request["end"])
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        raise ValueError("invalid full NAV code")
    with redirect_stdout(io.StringIO()):
        result = _fetch_payload(code, end)
        validate_full_payload(result, code, now_shanghai())
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()

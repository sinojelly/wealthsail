"""AKShare unit-NAV fallback isolated from the research process by a deadline.

Both adapters return disclosed fund unit NAV. The listed-fund adapter is also
useful for LOFs; its output is never substituted with exchange close prices.
"""

from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys

from backend.integrations.market_data import Observation, fetch_fund_nav_history
from backend.integrations.nav_history_proof import nav_points_digest

logger = logging.getLogger("wealthsail.fund_nav_fallback")
_API = "https://api.fund.eastmoney.com/f10/lsjz"


def _frame_rows(frame, start, end):
    if "净值日期" not in frame.columns or "单位净值" not in frame.columns:
        raise ValueError("AKShare response has no disclosed unit NAV")
    rows, received = {}, 0
    for raw in frame.to_dict("records"):
        value = raw["单位净值"]
        try:
            if isinstance(value, bool) or value is None:
                raise ValueError("invalid disclosed unit NAV")
            day = raw["净值日期"]
            day = (
                day.date()
                if isinstance(day, datetime)
                else date.fromisoformat(str(day))
            )
            number = Decimal(str(value))
            if not number.is_finite() or number <= 0:
                raise ValueError("invalid disclosed unit NAV")
        except (TypeError, InvalidOperation) as exc:
            raise ValueError("invalid disclosed unit NAV") from exc
        if not start <= day <= end:
            continue
        received += 1
        if day in rows and Decimal(rows[day]["close"]) != number:
            raise ValueError("conflicting disclosed unit NAV")
        rows.setdefault(day, {"date": day.isoformat(), "close": str(number)})
    return [rows[day] for day in sorted(rows)], received


def _verify_akshare_pages(pages, rows):
    """Observe the adapter's actual responses rather than trusting a dataframe."""
    if not pages:
        raise RuntimeError("AKShare did not expose complete NAV pagination")
    total, size = pages[0]["TotalCount"], pages[0]["PageSize"]
    if type(total) is not int or type(size) is not int or total <= 0 or size <= 0:
        raise RuntimeError("AKShare NAV pagination is not complete")
    count = (total + size - 1) // size
    unique = {}
    for page in pages:
        if (
            page.get("TotalCount") != total
            or page.get("PageSize") != size
            or type(page.get("PageIndex")) is not int
            or not isinstance(page.get("Data", {}).get("LSJZList"), list)
        ):
            raise RuntimeError("AKShare NAV pagination changed")
        index = page["PageIndex"]
        if index in unique and unique[index] != page:
            raise RuntimeError("AKShare NAV repeated conflicting page")
        unique[index] = page
    if set(unique) != set(range(1, count + 1)):
        raise RuntimeError("AKShare NAV pagination is not complete")
    received = [item for i in sorted(unique) for item in unique[i]["Data"]["LSJZList"]]
    if len(received) != total:
        raise RuntimeError("AKShare NAV pagination is not complete")
    by_day = {}
    for row in received:
        try:
            day = date.fromisoformat(row["FSRQ"]).isoformat()
            value = Decimal(str(row["DWJZ"]))
            if not value.is_finite() or value <= 0:
                raise ValueError("invalid disclosed unit NAV")
            if day in by_day and by_day[day] != value:
                raise ValueError("conflicting disclosed unit NAV")
            by_day[day] = value
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise ValueError("invalid disclosed unit NAV") from exc
    actual = {row["date"]: Decimal(row["close"]) for row in rows}
    if actual != by_day:
        raise RuntimeError("AKShare NAV dataframe differs from complete response")
    return {
        "totalCount": total,
        "receivedRowCount": len(received),
        "pageCount": count,
        "requiredPages": count,
    }


def _fetch_akshare_payload(symbol, start, end, minimum_points):
    import akshare as ak
    import requests

    candidates = []
    try:
        frame = ak.fund_open_fund_info_em(symbol=symbol, indicator="单位净值走势")
        rows, received = _frame_rows(frame, start, end)
        if rows:
            candidates.append(
                {
                    "adapter": "fund_open_fund_info_em",
                    "sourceUri": f"https://fund.eastmoney.com/pingzhongdata/{symbol}.js",
                    "rows": rows,
                    "complete": True,
                    "proofScope": "RETURNED_UNIT_NAV_SERIES",
                    "totalCount": received,
                    "receivedRowCount": received,
                    "pageCount": 1,
                    "requiredPages": 1,
                }
            )
        if len(rows) >= minimum_points:
            return candidates[0]
    except Exception:  # noqa: BLE001 -- try the public listed-fund NAV adapter independently
        pass

    pages, original_get = [], requests.get

    def observed_get(url, *args, **kwargs):
        response = original_get(url, *args, **kwargs)
        response.raise_for_status()
        if url == _API and str((kwargs.get("params") or {}).get("fundCode")) == symbol:
            pages.append(response.json())
        return response

    try:
        requests.get = observed_get
        frame = ak.fund_etf_fund_info_em(
            fund=symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        rows, _ = _frame_rows(frame, start, end)
        pagination = _verify_akshare_pages(pages, rows)
        if rows:
            candidates.append(
                {
                    "adapter": "fund_etf_fund_info_em",
                    "sourceUri": _API,
                    "rows": rows,
                    "complete": True,
                    "proofScope": "RETURNED_UNIT_NAV_SERIES",
                    **pagination,
                }
            )
    except Exception:  # noqa: BLE001 -- keep a valid earlier open-fund response
        if not candidates:
            raise RuntimeError(
                "AKShare did not provide complete unit NAV history"
            ) from None
    finally:
        requests.get = original_get
    if not candidates:
        raise RuntimeError("AKShare did not provide complete unit NAV history")
    return max(
        candidates, key=lambda item: (item["rows"][-1]["date"], len(item["rows"]))
    )


async def _run_akshare(symbol, start, end, *, minimum_points, timeout_seconds):
    # Provider subprocesses do not need database, model or webhook credentials.
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
        "backend.integrations.fund_nav_fallback",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    request = json.dumps(
        {
            "symbol": symbol,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "minimumPoints": minimum_points,
        }
    ).encode()
    try:
        output, _ = await asyncio.wait_for(
            process.communicate(request), timeout=timeout_seconds
        )
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode != 0 or len(output) > 2_000_000:
        raise RuntimeError("AKShare unit NAV subprocess failed")
    return json.loads(output)


def _observations(payload, symbol, start, end):
    adapter = payload.get("adapter")
    expected = {
        "fund_open_fund_info_em": f"https://fund.eastmoney.com/pingzhongdata/{symbol}.js",
        "fund_etf_fund_info_em": _API,
    }
    if (
        payload.get("complete") is not True
        or adapter not in expected
        or payload.get("sourceUri") != expected[adapter]
    ):
        raise ValueError("invalid AKShare unit NAV provenance")
    by_day = {}
    for point in payload["rows"]:
        day = date.fromisoformat(point["date"])
        value = Decimal(str(point["close"]))
        if not start <= day <= end or not value.is_finite() or value <= 0:
            raise ValueError("invalid AKShare unit NAV coordinate")
        if day in by_day and by_day[day] != value:
            raise ValueError("conflicting AKShare unit NAV coordinate")
        by_day[day] = value
    points = [
        {"date": day.isoformat(), "close": str(by_day[day])} for day in sorted(by_day)
    ]
    if not points:
        return []
    retained = points[-500:]
    proof = {
        "methodVersion": "akshare-unit-nav-history-v1",
        "fundCode": symbol,
        "sourceUri": expected[adapter],
        "adapter": adapter,
        "proofScope": "RETURNED_UNIT_NAV_SERIES",
        "queryStart": start.isoformat(),
        "queryEnd": end.isoformat(),
        "totalCount": payload.get("totalCount", len(points)),
        "receivedRowCount": payload.get("receivedRowCount", len(points)),
        "uniquePointCount": len(points),
        "pointCount": len(retained),
        "pageCount": payload.get("pageCount", 1),
        "requiredPages": payload.get("requiredPages", 1),
        "complete": True,
        "firstDate": retained[0]["date"],
        "lastDate": retained[-1]["date"],
        "pointsDigest": nav_points_digest(retained),
        "retrievedAt": datetime.now(timezone.utc).isoformat(),
    }
    return [
        Observation(
            symbol=symbol,
            subject_type="FUND",
            field_code="NAV",
            value=by_day[day],
            as_of=datetime.combine(day, time.min, tzinfo=timezone.utc),
            quality_status="STALE",
            unit="CNY/share",
            method_version=f"akshare/{adapter}/unit-nav-v1",
            origin_uri=expected[adapter],
            metadata={"officialUnitNav": True, "providerHistoryProof": proof},
        )
        for day in sorted(by_day)
    ]


def _complete_history(rows, symbol, start, end):
    """Row count alone cannot certify provider completeness or product identity."""
    if not rows:
        return False
    try:
        proof = rows[0].metadata.get("providerHistoryProof")
        if not isinstance(proof, dict) or proof.get("complete") is not True:
            return False
        allowed = {
            ("eastmoney-nav-history-v1", _API),
            ("akshare-unit-nav-history-v1", _API),
            (
                "akshare-unit-nav-history-v1",
                f"https://fund.eastmoney.com/pingzhongdata/{symbol}.js",
            ),
        }
        if (
            (proof.get("methodVersion"), proof.get("sourceUri")) not in allowed
            or proof.get("fundCode") != symbol
            or proof.get("queryStart") != start.isoformat()
            or proof.get("queryEnd") != end.isoformat()
        ):
            return False
        fields = (
            "totalCount",
            "receivedRowCount",
            "uniquePointCount",
            "pointCount",
            "pageCount",
            "requiredPages",
        )
        if any(type(proof.get(key)) is not int or proof[key] <= 0 for key in fields):
            return False
        if (
            proof["receivedRowCount"] != proof["totalCount"]
            or proof["totalCount"] < len(rows)
            or proof["uniquePointCount"] != len(rows)
            or proof["pointCount"] != min(500, len(rows))
            or proof["pageCount"] != proof["requiredPages"]
        ):
            return False
        points, previous = [], None
        for row in rows:
            metadata = row.metadata or {}
            day = row.as_of.date()
            if (
                row.symbol != symbol
                or row.subject_type != "FUND"
                or row.field_code != "NAV"
                or row.quality_status not in {"FRESH", "STALE", "REVISED"}
                or row.as_of.tzinfo is None
                or row.as_of.utcoffset() is None
                or metadata.get("officialUnitNav") is not True
                or metadata.get("providerHistoryProof") != proof
                or not start <= day <= end
                or previous is not None
                and day <= previous
            ):
                return False
            points.append({"date": day.isoformat(), "close": row.value})
            previous = day
        retained = points[-500:]
        retrieved = datetime.fromisoformat(proof["retrievedAt"])
        current = datetime.now(timezone.utc)
        return (
            retrieved.tzinfo is not None
            and retrieved.utcoffset() is not None
            and 0 <= (current - retrieved).total_seconds() <= 36 * 3600
            and proof.get("firstDate") == retained[0]["date"]
            and proof.get("lastDate") == retained[-1]["date"]
            and proof.get("pointsDigest") == nav_points_digest(retained)
        )
    except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation):
        return False


async def fetch_fund_nav_history_with_fallback(
    symbol,
    start,
    end,
    client,
    *,
    page_size=100,
    max_pages=200,
    minimum_points=220,
    timeout_seconds=30,
):
    """Use AKShare when primary data cannot prove its complete unit-NAV response."""
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol) or start > end:
        raise ValueError("invalid fund NAV history interval")
    if (
        type(minimum_points) is not int
        or minimum_points <= 0
        or timeout_seconds <= 0
        or type(page_size) is not int
        or page_size <= 0
        or type(max_pages) is not int
        or max_pages <= 0
    ):
        raise ValueError("invalid fund NAV fallback bound")
    primary = []
    try:
        primary = await fetch_fund_nav_history(
            symbol, start, end, client, page_size=page_size, max_pages=max_pages
        )
        # A proven complete short response is still short history. Asking the
        # same upstream through another adapter cannot create missing history.
        if _complete_history(primary, symbol, start, end):
            return primary
    except Exception as exc:  # noqa: BLE001 -- provider failures have a bounded alternative
        logger.info(
            "fund_nav_primary_failed fund_code=%s error_class=%s",
            symbol,
            type(exc).__name__,
        )
    try:
        payload = await _run_akshare(
            symbol,
            start,
            end,
            minimum_points=minimum_points,
            timeout_seconds=timeout_seconds,
        )
        alternative = _observations(payload, symbol, start, end)
        if _complete_history(alternative, symbol, start, end):
            return alternative
        raise ValueError("AKShare response lacks complete unit NAV proof")
    except Exception as exc:  # noqa: BLE001 -- retain already fetched actual primary facts
        logger.info(
            "fund_nav_fallback_failed fund_code=%s error_class=%s",
            symbol,
            type(exc).__name__,
        )
        if not primary:
            raise RuntimeError("public unit NAV sources unavailable") from None
    return primary


def main():
    request = json.loads(sys.stdin.buffer.read(4096))
    symbol = request["symbol"]
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("invalid fund NAV code")
    start, end = (
        date.fromisoformat(request["start"]),
        date.fromisoformat(request["end"]),
    )
    if start > end:
        raise ValueError("invalid fund NAV interval")
    # Keep progress or provider diagnostic output out of the JSON protocol.
    with redirect_stdout(io.StringIO()):
        result = _fetch_akshare_payload(symbol, start, end, request["minimumPoints"])
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()

"""Current public news drives both the preflight and the eventual analysis pool."""

import asyncio
from datetime import timedelta

from backend.common.database import system_connection
from backend.macro.alpha_repository import read_alpha_public, local


def research_catalysts(bundle, external):
    return [
        row
        for row in [
            *(bundle or {}).get("catalysts", []),
            *(external or {}).get("documents", []),
            *(external or {}).get("candidateIndex", []),
        ]
        if isinstance(row, dict)
    ]


async def completed_etf_dates(conn, now):
    now = local(now)
    end = now.date() if now.hour >= 18 else now.date() - timedelta(days=1)
    start = end - timedelta(days=20)
    rows = await conn.fetch(
        """SELECT calendar_date,is_trading_day FROM reference.trading_calendars
        WHERE market='SSE' AND calendar_date BETWEEN $1 AND $2 ORDER BY calendar_date""",
        start,
        end,
    )
    facts = {row["calendar_date"]: row["is_trading_day"] for row in rows}
    if len(facts) != len(rows) or any(
        type(facts.get(start + timedelta(days=i))) is not bool
        for i in range((end - start).days + 1)
    ):
        return []
    return sorted(day for day, trading in facts.items() if trading)[-2:]


async def prepare_research_inputs(
    pool,
    now,
    external,
    *,
    debug_request_id=None,
    manual_request_id=None,
    source_batch_id=None,
):
    from backend.investment.discovery import collect_discovery_nav_histories
    from backend.integrations.etf_flows import fetch_etf_flow_signals

    async with system_connection(pool) as conn:
        bundle = await read_alpha_public(
            conn,
            now,
            **(
                {"source_batch_id": source_batch_id}
                if source_batch_id is not None
                else {}
            ),
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
        dates = await completed_etf_dates(conn, now)

    # Never hold a DB connection while a public supplier is queried.
    async def frozen_nav():
        return {
            "fundHistories": bundle.get("fundHistories", []),
            "themeNavSourceResults": bundle.get("themeNavSourceResults", {}),
        }

    nav, flows = await asyncio.gather(
        frozen_nav()
        if source_batch_id is not None
        else collect_discovery_nav_histories(
            pool, now, catalysts=research_catalysts(bundle, external)
        ),
        fetch_etf_flow_signals(now, trading_dates=dates),
        return_exceptions=True,
    )
    result = dict(external)
    result["sourceResults"] = dict(external.get("sourceResults") or {})
    if isinstance(nav, BaseException):
        if isinstance(nav, asyncio.CancelledError):
            raise nav
        result["sourceResults"]["CORE_FUND_NAV"] = {
            "status": "FAILED",
            "errorClass": type(nav).__name__,
        }
    else:
        result["dynamicNavSourceResults"] = nav.get("themeNavSourceResults", {})
        if isinstance(nav.get("fundMasterSourceResult"), dict):
            result["sourceResults"]["DISCOVERY_FUND_MASTER"] = nav[
                "fundMasterSourceResult"
            ]
    if isinstance(flows, BaseException):
        if isinstance(flows, asyncio.CancelledError):
            raise flows
        result["sourceResults"]["ETF_FLOWS"] = {
            "status": "FAILED",
            "errorClass": type(flows).__name__,
        }
    else:
        result["etfFlowSignals"] = flows
        result["sourceResults"].update(flows.get("sourceResults", {}))
    return result

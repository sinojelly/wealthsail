"""Market service: fetch → persist → recompute orchestration.

Flow (per session):
  1. read active instruments + current holdings (worker pool, system connection);
  2. fetch observations from AkShare / 天天基金 (parallel, 10s timeouts);
  3. persist one source snapshot + normalized observations;
  4. for every tenant holding the instruments: recompute position snapshots
     and account NAV snapshots inside a per-tenant RLS transaction.

Quality flows through unchanged: FRESH quotes / ESTIMATED fund estimates /
STALE published NAVs / UNAVAILABLE on failure — projections never upgrade
quality beyond what the source reported.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from backend.common.database import Pool, system_connection, tenant_transaction
from backend.config import Settings
from backend.dependencies import now_shanghai
from backend.integrations import market_data as md
from backend.market import repository
from backend.market.fund_estimator import FundComponentReturn, estimate_fund_nav

logger = logging.getLogger("wealthsail.market")

# field_code priority when an instrument has multiple observations (best wins)
_FIELD_PRIORITY = {
    md.F_CLOSE: 0,
    md.F_ESTIMATED_NAV: 0,
    md.F_NAV: 1,
}
_QUALITY_PRIORITY = {
    md.Q_FRESH: 0,
    md.Q_ESTIMATED: 1,
    md.Q_STALE: 2,
    md.Q_UNAVAILABLE: 3,
}

def _source_name(observation: md.Observation) -> str:
    if observation.field_code == md.F_ESTIMATED_NAV:
        return md.ESTIMATOR_SOURCE_NAME
    if observation.subject_type == "FUND":
        return md.FUNDSOURCE_NAME
    return md.AKSOURCE_NAME


async def refresh_market(
    pool: Pool,
    settings: Settings,
    session: str,
) -> dict:
    """Execute one market-refresh cycle for a four-session slot."""
    started = now_shanghai()
    result: dict = {"session": session, "startedAt": started}

    async with system_connection(pool) as conn:
        for source_name in md.source_names():
            await repository.upsert_data_source(
                conn,
                name=source_name,
                source_type=(
                    "FUND_NAV"
                    if source_name == md.ESTIMATOR_SOURCE_NAME
                    else "MARKET_DATA"
                ),
            )
        instruments = await repository.get_active_instruments(conn)
        holdings = await repository.get_holdings_for_refresh(conn)
        source_ids = {
            name: await repository.get_source_id(conn, name)
            for name in md.source_names()
        }

    if not instruments:
        logger.info("无活跃标的，跳过行情刷新")
        return {**result, "observations": 0, "tenantsUpdated": 0}

    # instrument_id → ticker, asset_type
    by_id = {str(i["instrument_id"]): i for i in instruments}
    ticker_to_id = {
        i["ticker"]: str(i["instrument_id"])
        for i in instruments
        if i["ticker"]
    }

    # Scope: INTRADAY → holdings only; FUND_1430 → funds only; others → all.
    stock_symbols = {i["ticker"] for i in instruments if i["asset_type"] == "CN_STOCK"}
    etf_symbols = {i["ticker"] for i in instruments if i["asset_type"] == "EXCHANGE_FUND"}
    fund_symbols = {i["ticker"] for i in instruments if i["asset_type"] == "OFF_EXCHANGE_FUND"}

    if session == "INTRADAY":
        held = {str(h["instrument_id"]) for h in holdings}
        held = {i["ticker"] for iid, i in by_id.items() if iid in held and i["ticker"]}
        stock_symbols &= held
        etf_symbols &= held
        fund_symbols &= held
    fund_disclosures: list[dict] = []
    if session == "FUND_1430":
        held_ids = {str(h["instrument_id"]) for h in holdings}
        held_funds = {
            str(i["instrument_id"]): i["ticker"]
            for i in instruments
            if i["asset_type"] == "OFF_EXCHANGE_FUND"
            and str(i["instrument_id"]) in held_ids
            and i["ticker"]
        }
        fund_symbols = set(held_funds.values())
        fetched_disclosures = await md.fetch_fund_disclosures(list(fund_symbols))
        async with system_connection(pool) as conn:
            await repository.upsert_fund_disclosures(
                conn,
                [
                    (
                        ticker_to_id[item.fund_symbol],
                        item.component_symbol,
                        item.component_name,
                        item.weight,
                        item.disclosed_on,
                        item.disclosure_label,
                        source_ids[md.FUNDSOURCE_NAME],
                    )
                    for item in fetched_disclosures
                    if item.fund_symbol in ticker_to_id
                ],
            )
            stored = await repository.get_latest_fund_disclosures(
                conn, list(held_funds)
            )
        fund_disclosures = [dict(row) for row in stored]

        # Price only the disclosed components understood by the instrument
        # master.  Unsupported/HK components remain in disclosure metadata and
        # reduce coverage instead of silently disappearing.
        component_codes = {
            row["component_code"] for row in fund_disclosures
            if row["component_code"] in ticker_to_id
        }
        stock_symbols = {
            code for code in component_codes
            if by_id[ticker_to_id[code]]["asset_type"] == "CN_STOCK"
        }
        etf_symbols = {
            code for code in component_codes
            if by_id[ticker_to_id[code]]["asset_type"] == "EXCHANGE_FUND"
        }

    observations = await md.fetch_all(
        list(stock_symbols), list(etf_symbols), list(fund_symbols)
    )
    if session == "FUND_1430":
        observations.extend(
            _build_fund_estimates(
                observations=observations,
                disclosures=fund_disclosures,
                fund_instrument_ids=set(held_funds),
                by_id=by_id,
                as_of=started,
            )
        )
    result["fetched"] = len(observations)
    usable = [o for o in observations if o.quality_status != md.Q_UNAVAILABLE]
    result["usable"] = len(usable)

    if not observations:
        logger.warning("行情抓取无任何返回（含降级）")
        return {**result, "observations": 0, "tenantsUpdated": 0}

    # ---- persist: source snapshot + observations ---------------------------
    async with system_connection(pool) as conn:
        quality = md.Q_FRESH if any(o.quality_status == md.Q_FRESH for o in observations) else (
            md.Q_STALE if any(o.quality_status == md.Q_STALE for o in observations) else md.Q_UNAVAILABLE
        )
        retrieval_key = f"{session}:{started.strftime('%Y%m%d%H%M%S')}"
        for source_name in md.source_names():
            batch = [o for o in observations if _source_name(o) == source_name]
            source_id = source_ids.get(source_name)
            if not batch or not source_id:
                continue
            snapshot_id = await repository.insert_source_snapshot(
                conn,
                source_id=source_id,
                retrieval_key=f"{retrieval_key}:{source_name}",
                observations=[o.to_payload() for o in batch],
                quality_status=quality,
                retrieved_at=started,
            )
            rows = [
                (
                    o.subject_type,
                    ticker_to_id.get(o.symbol),  # subject_id (instrument uuid)
                    o.field_code,
                    o.value,
                    o.lower_value,
                    o.upper_value,
                    o.unit,
                    o.currency,
                    o.as_of,
                    started,
                    snapshot_id,
                    source_id,
                    o.quality_status,
                    o.method_version,
                    json.dumps(o.metadata or {}, ensure_ascii=False, default=str),
                )
                for o in batch
                if o.symbol in ticker_to_id
            ]
            await repository.insert_observations(conn, rows)
    result["observations"] = len(usable)

    # ---- recompute projections per tenant -----------------------------------
    quotes_by_instrument: dict[str, dict] = {}
    for obs in observations:
        instrument_id = ticker_to_id.get(obs.symbol)
        if not instrument_id:
            continue
        current = quotes_by_instrument.get(instrument_id)
        if current is None or (
            _FIELD_PRIORITY.get(obs.field_code, 9) < _FIELD_PRIORITY.get(current["field"], 9)
        ) or (
            _FIELD_PRIORITY.get(obs.field_code, 9) == _FIELD_PRIORITY.get(current["field"], 9)
            and _QUALITY_PRIORITY.get(obs.quality_status, 9) < _QUALITY_PRIORITY.get(current["quality"], 9)
        ):
            quotes_by_instrument[instrument_id] = {
                "field": obs.field_code,
                "price": obs.value if obs.quality_status != md.Q_UNAVAILABLE else None,
                "quality": _quality_to_snapshot(obs.quality_status),
            }

    tenants = {str(h["tenant_id"]) for h in holdings}
    as_of_date = started.date()
    tenants_updated = 0
    for tenant_id in tenants:
        owner = await _tenant_owner_user(pool, tenant_id)
        if owner is None:
            continue
        try:
            async with tenant_transaction(pool, owner, tenant_id) as conn:
                positions_updated = await repository.refresh_positions(
                    conn,
                    tenant_id=tenant_id,
                    quotes=quotes_by_instrument,
                    as_of_date=as_of_date,
                )
                if positions_updated:
                    await repository.refresh_account_nav(
                        conn, tenant_id=tenant_id, as_of_date=as_of_date
                    )
            tenants_updated += 1
        except Exception:  # noqa: BLE001 — per-tenant isolation
            logger.exception("tenant %s 快照重算失败", tenant_id)

    result["tenantsUpdated"] = tenants_updated
    logger.info(
        "行情刷新 %s: 抓取 %d / 有效 %d, 更新租户 %d",
        session, len(observations), len(usable), tenants_updated,
    )
    return result


def _build_fund_estimates(
    *,
    observations: list[md.Observation],
    disclosures: list[dict],
    fund_instrument_ids: set[str],
    by_id: dict[str, object],
    as_of: datetime,
) -> list[md.Observation]:
    """Join official NAV, dated disclosures and live component returns."""
    nav_by_symbol = {
        obs.symbol: obs
        for obs in observations
        if obs.field_code == md.F_NAV
        and obs.quality_status != md.Q_UNAVAILABLE
        and obs.value is not None
    }
    returns_by_symbol = {
        obs.symbol: obs.return_rate
        for obs in observations
        if obs.return_rate is not None and obs.quality_status == md.Q_FRESH
    }
    by_fund: dict[str, list[dict]] = defaultdict(list)
    for row in disclosures:
        by_fund[str(row["fund_instrument_id"])].append(row)

    estimates: list[md.Observation] = []
    for fund_id in fund_instrument_ids:
        rows = by_fund.get(fund_id, [])
        fund = by_id.get(fund_id)
        fund_symbol = fund["ticker"] if fund else None
        if not fund_symbol:
            continue
        nav = nav_by_symbol.get(fund_symbol)
        disclosure_date = max((row["disclosed_on"] for row in rows), default=None)
        disclosure_label = rows[0]["disclosure_label"] if rows else None
        if nav is None or not rows:
            reason = (
                "最近正式净值不可用，系统已弃权。"
                if nav is None
                else "未取得基金持仓披露，系统已弃权。"
            )
            estimates.append(
                md.Observation(
                    symbol=fund_symbol,
                    subject_type="FUND",
                    field_code=md.F_ESTIMATED_NAV,
                    value=None,
                    as_of=as_of,
                    quality_status=md.Q_UNAVAILABLE,
                    method_version="weighted-disclosed-holdings/v1",
                    metadata={
                        "coveragePct": "0.00",
                        "disclosedOn": str(disclosure_date),
                        "disclosureLabel": disclosure_label,
                        "explanation": reason,
                    },
                )
            )
            continue
        components = tuple(
            FundComponentReturn(
                instrument_code=row["component_code"],
                weight=Decimal(str(row["weight"])),
                return_rate=returns_by_symbol[row["component_code"]],
            )
            for row in rows
            if row["component_code"] in returns_by_symbol
        )
        estimate = estimate_fund_nav(
            fund_code=fund_symbol,
            confirmed_nav=nav.value,
            components=components,
            residual_benchmark_return=Decimal("0"),
            as_of=as_of,
        )
        estimates.append(
            md.Observation(
                symbol=fund_symbol,
                subject_type="FUND",
                field_code=md.F_ESTIMATED_NAV,
                value=estimate.value,
                lower_value=estimate.lower_value,
                upper_value=estimate.upper_value,
                as_of=as_of,
                quality_status=estimate.quality_status,
                unit="元/份",
                method_version=estimate.method_version,
                origin_uri="internal:fund-estimator",
                metadata={
                    "coveragePct": str(estimate.coverage_pct),
                    "disclosedOn": str(disclosure_date),
                    "disclosureLabel": disclosure_label,
                    "confirmedNav": str(nav.value),
                    "confirmedNavAsOf": nav.as_of.isoformat(),
                    "residualBenchmarkReturn": "0",
                    "explanation": estimate.explanation,
                },
            )
        )
    return estimates


def _quality_to_snapshot(quality: str) -> str:
    """Observation quality → position_snapshot data_quality_status."""
    return quality if quality in ("FRESH", "ESTIMATED", "STALE", "UNAVAILABLE") else "UNAVAILABLE"


async def _tenant_owner_user(pool: Pool, tenant_id: str) -> str | None:
    async with system_connection(pool) as conn:
        return await conn.fetchval(
            """
            SELECT user_id FROM identity.tenant_members
            WHERE tenant_id = $1 AND role = 'OWNER' AND status = 'ACTIVE'
            ORDER BY created_at ASC LIMIT 1
            """,
            tenant_id,
        )


async def fill_trading_calendar(pool: Pool) -> int:
    """Backfill 2024–2026 SSE trading calendar from the Sina exchange calendar.

    2024→today: known official dates; workdays absent from the official list are
    holidays (false). Future dates (official calendar not yet published): workday
    fallback true, to be corrected on the next run after publication.
    """
    import asyncio


    def _fetch_dates() -> set[str]:
        import akshare

        df = akshare.tool_trade_date_hist_sina()
        return {str(d)[:10] for d in df["trade_date"].tolist()}

    try:
        official = await asyncio.to_thread(_fetch_dates)
    except Exception as exc:  # noqa: BLE001
        logger.error("交易日历拉取失败: %s", exc)
        return 0

    today = date.today()
    rows: list[tuple] = []
    cursor = date(2024, 1, 1)
    end = date(2026, 12, 31)
    while cursor <= end:
        if cursor.weekday() >= 5:
            rows.append((cursor, False))
        elif cursor.isoformat() in official:
            rows.append((cursor, True))
        else:
            # 2024..today 的缺失工作日 = 官方节假日；未来日期 = 工作日兜底
            rows.append((cursor, cursor > today))
        cursor += date.resolution  # timedelta(days=1)

    async with system_connection(pool) as conn:
        inserted = await repository.upsert_trading_calendar_rows(conn, rows)
    logger.info("交易日历填充完成: %d 天 (2024-2026, SSE)", inserted)
    return inserted

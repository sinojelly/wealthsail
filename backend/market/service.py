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

import logging
from datetime import date, datetime
from decimal import Decimal

from backend.common.database import Pool, system_connection, tenant_transaction
from backend.config import Settings
from backend.dependencies import now_shanghai
from backend.integrations import market_data as md
from backend.market import repository

logger = logging.getLogger("wealthsail.market")

# field_code priority when an instrument has multiple observations (best wins)
_FIELD_PRIORITY = {
    md.F_CLOSE: 0,
    md.F_NAV: 0,
}
_QUALITY_PRIORITY = {
    md.Q_FRESH: 0,
    md.Q_STALE: 2,
    md.Q_UNAVAILABLE: 3,
}

SOURCE_NAME_BY_TYPE = {
    "INSTRUMENT": md.AKSOURCE_NAME,
    "FUND": md.FUNDSOURCE_NAME,
}


async def refresh_market(
    pool: Pool,
    settings: Settings,
    session: str,
) -> dict:
    """Execute one market-refresh cycle for a four-session slot."""
    started = now_shanghai()
    result: dict = {"session": session, "startedAt": started}

    async with system_connection(pool) as conn:
        await repository.upsert_data_source(
            conn, name=md.FUNDSOURCE_NAME, source_type="MARKET_DATA"
        )
        instruments = await repository.get_active_instruments(conn)
        holdings = await repository.get_holdings_for_refresh(conn)
        source_ids = {
            md.AKSOURCE_NAME: await repository.get_source_id(conn, md.AKSOURCE_NAME),
            md.FUNDSOURCE_NAME: await repository.get_source_id(conn, md.FUNDSOURCE_NAME),
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
    elif session == "FUND_1430":
        stock_symbols = set()
        etf_symbols = set()

    observations = await md.fetch_all(
        list(stock_symbols), list(etf_symbols), list(fund_symbols)
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
        for source_name in (md.AKSOURCE_NAME, md.FUNDSOURCE_NAME):
            batch = [o for o in observations if SOURCE_NAME_BY_TYPE.get(o.subject_type) == source_name]
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
                    o.unit,
                    o.currency,
                    o.as_of,
                    started,
                    snapshot_id,
                    source_id,
                    o.quality_status,
                    o.method_version,
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

    from backend.integrations import market_data as _md

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

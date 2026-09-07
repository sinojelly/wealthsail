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

import asyncio
import json
import logging
import re
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from backend.common.database import Pool, system_connection, tenant_transaction
from backend.config import Settings
from backend.dependencies import now_shanghai
from backend.integrations import market_data as md
from backend.market import repository
from backend.market.fund_estimator import FundComponentReturn, estimate_fund_nav

logger = logging.getLogger("wealthsail.market")

_MARKET_HOT_BOOTSTRAP_LOCK = "wealthsail.market-hot.bootstrap.v1"

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
        if observation.method_version == "sina/fund-estimate/v1":
            return md.SINA_FUND_ESTIMATE_SOURCE_NAME
        return md.ESTIMATOR_SOURCE_NAME
    if observation.subject_type == "FUND":
        return md.FUNDSOURCE_NAME
    return md.AKSOURCE_NAME


def _observation_row(
    observation: md.Observation,
    *,
    instrument_id: str,
    observed_at: datetime,
    snapshot_id: str,
    source_id: str,
) -> tuple:
    """Build the persistence row without dropping the provider day return.

    ``Observation.return_rate`` is a decimal fraction (``0.0125`` means
    ``1.25%``), while report queries consume ``metadata.changePct`` as a
    percentage.  Normalize that contract at the single database boundary so
    every equity, ETF and fund provider is persisted consistently.
    """
    metadata = dict(observation.metadata or {})
    if observation.return_rate is not None and observation.return_rate.is_finite():
        metadata["changePct"] = str(observation.return_rate * Decimal("100"))
    return (
        observation.subject_type,
        instrument_id,
        observation.field_code,
        observation.value,
        observation.lower_value,
        observation.upper_value,
        observation.unit,
        observation.currency,
        observation.as_of,
        observed_at,
        snapshot_id,
        source_id,
        observation.quality_status,
        observation.method_version,
        json.dumps(metadata, ensure_ascii=False, default=str),
    )


def _source_snapshot_quality(observations: list[md.Observation]) -> str:
    """Summarize one provider batch without upgrading or losing ESTIMATED."""
    for quality in (md.Q_FRESH, md.Q_ESTIMATED, md.Q_STALE, md.Q_UNAVAILABLE):
        if any(item.quality_status == quality for item in observations):
            return quality
    return md.Q_UNAVAILABLE


def _usable_estimate(observation: md.Observation | None) -> bool:
    value = observation.value if observation is not None else None
    return value is not None and value.is_finite() and value > 0


def _select_fund_estimates(
    *,
    provider_estimates: list[md.Observation],
    internal_estimates: list[md.Observation],
    fund_symbols: set[str],
) -> list[md.Observation]:
    """Choose one auditable estimate per fund using the documented chain.

    A same-day external estimate covers feeder/index funds that cannot reach a
    meaningful stock-disclosure coverage ratio.  A real internal estimate is
    preferred over an old external quote; a stale external value is retained
    only when no calculable internal value exists, and remains visibly STALE.
    """
    provider_by_symbol = {item.symbol: item for item in provider_estimates}
    internal_by_symbol = {item.symbol: item for item in internal_estimates}
    selected: list[md.Observation] = []
    for symbol in sorted(fund_symbols):
        provider = provider_by_symbol.get(symbol)
        internal = internal_by_symbol.get(symbol)
        if (
            _usable_estimate(provider)
            and provider is not None
            and provider.quality_status == md.Q_ESTIMATED
        ):
            selected.append(provider)
        elif internal is not None and _usable_estimate(internal):
            selected.append(internal)
        elif provider is not None and _usable_estimate(provider):
            selected.append(provider)
        elif internal is not None:
            selected.append(internal)
        elif provider is not None:
            selected.append(provider)
    return selected


def _normalize_latest_trading_day_estimates(
    estimates: list[md.Observation],
    *,
    latest_trading_date: date | None,
    requested_at: datetime,
) -> list[md.Observation]:
    """Classify provider estimates against the market calendar, not wall time.

    Sina returns the last trading day's final estimate on weekends and market
    holidays.  That is a valid *historical reference* for a manually requested
    report, although it is not a live estimate for the request date.  Metadata
    keeps this distinction explicit so renderers never call it "today's"
    estimate.  Older values remain STALE and unusable values remain
    UNAVAILABLE.
    """
    normalized: list[md.Observation] = []
    for estimate in estimates:
        valuation_date = estimate.as_of.date()
        metadata = dict(estimate.metadata or {})
        is_latest_market_day = (
            latest_trading_date is not None and valuation_date == latest_trading_date
        )
        non_trading_day_reference = (
            is_latest_market_day and requested_at.date() != latest_trading_date
        )
        metadata.update(
            {
                "valuationDate": valuation_date.isoformat(),
                "latestTradingDate": (
                    latest_trading_date.isoformat()
                    if latest_trading_date is not None
                    else None
                ),
                "valuationDateKind": (
                    "LATEST_TRADING_DAY"
                    if non_trading_day_reference
                    else "CURRENT_TRADING_DAY"
                    if is_latest_market_day
                    else "OUTDATED"
                ),
                "nonTradingDayReference": non_trading_day_reference,
            }
        )
        quality = estimate.quality_status
        if is_latest_market_day and _usable_estimate(estimate):
            quality = md.Q_ESTIMATED
        normalized.append(replace(estimate, quality_status=quality, metadata=metadata))
    return normalized


def _attach_confirmed_nav_dates(
    estimates: list[md.Observation], observations: list[md.Observation]
) -> list[md.Observation]:
    """Add the audited official-NAV date to external provider metadata."""
    nav_by_symbol = {
        item.symbol: item
        for item in observations
        if item.field_code == md.F_NAV
        and item.value is not None
        and item.quality_status != md.Q_UNAVAILABLE
    }
    result: list[md.Observation] = []
    for estimate in estimates:
        if estimate.method_version != "sina/fund-estimate/v1":
            result.append(estimate)
            continue
        nav = nav_by_symbol.get(estimate.symbol)
        metadata = dict(estimate.metadata or {})
        if nav is not None:
            metadata["confirmedNavAsOf"] = nav.as_of.isoformat()
        result.append(replace(estimate, metadata=metadata))
    return result


def _normalized_a_share_component_symbol(value: object) -> str | None:
    """Normalize one disclosed Shanghai, Shenzhen or Beijing stock code."""
    raw = str(value or "").strip().upper()
    suffix_match = re.fullmatch(r"(\d{6})\.(?:SH|SZ|BJ)", raw)
    code = suffix_match.group(1) if suffix_match else raw
    return code if re.fullmatch(r"[034689]\d{5}", code) else None


def _normalized_a_share_component_symbols(disclosures: list[dict]) -> set[str]:
    """Return Provider-ready A-share codes from dated fund disclosures.

    Disclosure components are estimation inputs, not WealthSail holdings. They
    therefore do not need to be inserted into the canonical instrument master.
    Unsupported markets remain in the disclosure rows and naturally reduce
    estimator coverage instead of being silently treated as zero-return assets.
    """
    symbols: set[str] = set()
    for row in disclosures:
        code = _normalized_a_share_component_symbol(row.get("component_code"))
        if code is not None:
            symbols.add(code)
    return symbols


async def refresh_market(
    pool: Pool,
    settings: Settings,
    session: str,
) -> dict:
    """Execute one market-refresh cycle for a four-session slot."""
    started = now_shanghai()
    result: dict = {"session": session, "startedAt": started}

    async with system_connection(pool) as conn:
        for source_name, source_type in md.source_definitions():
            await repository.upsert_data_source(
                conn,
                name=source_name,
                source_type=source_type,
            )
        instruments = await repository.get_active_instruments(conn)
        holdings = await repository.get_holdings_for_refresh(conn)
        source_ids = {
            name: await repository.get_source_id(conn, name)
            for name in md.source_names()
        }
        latest_trading_date = await conn.fetchval(
            """
            SELECT COALESCE(
                (
                    SELECT max(calendar.calendar_date)
                      FROM reference.trading_calendars calendar
                     WHERE calendar.market = 'SSE'
                       AND calendar.is_trading_day = TRUE
                       AND calendar.calendar_date <= $1
                ),
                (
                    SELECT max(
                        (observation.as_of AT TIME ZONE 'Asia/Shanghai')::date
                    )
                      FROM market.market_observations observation
                     WHERE observation.field_code = 'NAV'
                       AND observation.value IS NOT NULL
                       AND (observation.as_of AT TIME ZONE 'Asia/Shanghai')::date
                           <= $1
                )
            )
            """,
            started.date(),
        )

    if not instruments:
        logger.info("无活跃标的，跳过行情刷新")
        return {**result, "observations": 0, "tenantsUpdated": 0}

    # instrument_id → ticker, asset_type
    by_id = {str(i["instrument_id"]): i for i in instruments}
    ticker_to_id = {
        i["ticker"]: str(i["instrument_id"]) for i in instruments if i["ticker"]
    }

    # Scope: INTRADAY → holdings only; FUND_1430 → funds only; others → all.
    stock_symbols = {i["ticker"] for i in instruments if i["asset_type"] == "CN_STOCK"}
    etf_symbols = {
        i["ticker"] for i in instruments if i["asset_type"] == "EXCHANGE_FUND"
    }
    fund_symbols = {
        i["ticker"] for i in instruments if i["asset_type"] == "OFF_EXCHANGE_FUND"
    }

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

        # Dated disclosure components are transient estimator inputs.  Fetch
        # recognized A-share codes even when a user has never held them and no
        # canonical instrument-master row exists. Raw component observations
        # remain auditable in the source snapshot; only the derived fund
        # estimate is normalized into market_observations.
        stock_symbols = _normalized_a_share_component_symbols(fund_disclosures)
        etf_symbols = set()

    provider_estimates: list[md.Observation] = []
    if session == "FUND_1430":
        observations, provider_estimates = await asyncio.gather(
            md.fetch_all(list(stock_symbols), list(etf_symbols), list(fund_symbols)),
            md.fetch_fund_intraday_estimates(list(fund_symbols)),
        )
        provider_estimates = _normalize_latest_trading_day_estimates(
            provider_estimates,
            latest_trading_date=latest_trading_date,
            requested_at=started,
        )
        internal_estimates = _build_fund_estimates(
            observations=observations,
            disclosures=fund_disclosures,
            fund_instrument_ids=set(held_funds),
            by_id=by_id,
            as_of=started,
        )
        selected_estimates = _select_fund_estimates(
            provider_estimates=provider_estimates,
            internal_estimates=internal_estimates,
            fund_symbols=fund_symbols,
        )
        selected_estimates = _attach_confirmed_nav_dates(
            selected_estimates, observations
        )
        observations.extend(selected_estimates)
        result["fundEstimateRequested"] = len(fund_symbols)
        result["fundEstimateUsable"] = sum(
            1
            for item in selected_estimates
            if item.quality_status != md.Q_UNAVAILABLE and item.value is not None
        )
        result["fundEstimateMethods"] = dict(
            sorted(
                {
                    method: sum(
                        1
                        for item in selected_estimates
                        if item.method_version == method
                    )
                    for method in {item.method_version for item in selected_estimates}
                }.items()
            )
        )
        logger.info(
            "14:30基金估值选择: 请求 %d 只, 可用 %d 只, 方法=%s, 不可用=%s",
            result["fundEstimateRequested"],
            result["fundEstimateUsable"],
            result["fundEstimateMethods"],
            ",".join(
                item.symbol
                for item in selected_estimates
                if item.quality_status == md.Q_UNAVAILABLE or item.value is None
            )
            or "-",
        )
    else:
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
                quality_status=_source_snapshot_quality(batch),
                retrieved_at=started,
            )
            rows = [
                _observation_row(
                    o,
                    instrument_id=ticker_to_id[o.symbol],
                    observed_at=started,
                    snapshot_id=snapshot_id,
                    source_id=source_id,
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
        if (
            current is None
            or (
                _FIELD_PRIORITY.get(obs.field_code, 9)
                < _FIELD_PRIORITY.get(current["field"], 9)
            )
            or (
                _FIELD_PRIORITY.get(obs.field_code, 9)
                == _FIELD_PRIORITY.get(current["field"], 9)
                and _QUALITY_PRIORITY.get(obs.quality_status, 9)
                < _QUALITY_PRIORITY.get(current["quality"], 9)
            )
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
        session,
        len(observations),
        len(usable),
        tenants_updated,
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
        covered_components: list[FundComponentReturn] = []
        for row in rows:
            normalized_component = _normalized_a_share_component_symbol(
                row["component_code"]
            )
            if normalized_component not in returns_by_symbol:
                continue
            covered_components.append(
                FundComponentReturn(
                    instrument_code=normalized_component,
                    weight=Decimal(str(row["weight"])),
                    return_rate=returns_by_symbol[normalized_component],
                )
            )
        estimate = estimate_fund_nav(
            fund_code=fund_symbol,
            confirmed_nav=nav.value,
            components=tuple(covered_components),
            residual_benchmark_return=Decimal("0"),
            as_of=as_of,
        )
        # 持仓披露超过 90 天（约一个完整披露季）时，即使计算成功也标记 STALE：
        # 中心值与区间仍展示，但说明参考价值有限，绝不把旧披露当作新鲜数据。
        quality_status = estimate.quality_status
        explanation = estimate.explanation
        if estimate.value is not None and disclosure_date is not None:
            disclosure_age_days = (as_of.date() - disclosure_date).days
            if disclosure_age_days > 90:
                quality_status = md.Q_STALE
                explanation = (
                    f"持仓披露日为 {disclosure_date}（距今 {disclosure_age_days} 天，"
                    "超过 90 天），估值参考价值有限。"
                ) + explanation
        estimates.append(
            md.Observation(
                symbol=fund_symbol,
                subject_type="FUND",
                field_code=md.F_ESTIMATED_NAV,
                value=estimate.value,
                lower_value=estimate.lower_value,
                upper_value=estimate.upper_value,
                return_rate=(estimate.value / nav.value - Decimal("1"))
                if estimate.value is not None and nav.value is not None
                else None,
                as_of=as_of,
                quality_status=quality_status,
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
                    "explanation": explanation,
                },
            )
        )
    return estimates


def _quality_to_snapshot(quality: str) -> str:
    """Observation quality → position_snapshot data_quality_status."""
    return (
        quality
        if quality in ("FRESH", "ESTIMATED", "STALE", "UNAVAILABLE")
        else "UNAVAILABLE"
    )


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

    def _fetch_dates() -> set[str]:
        import akshare

        df = akshare.tool_trade_date_hist_sina()
        return {str(d)[:10] for d in df["trade_date"].tolist()}

    try:
        official = await asyncio.to_thread(_fetch_dates)
    except Exception as exc:  # noqa: BLE001
        logger.error("交易日历拉取失败: %s", exc)
        return 0

    today = now_shanghai().date()
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


async def ensure_trading_calendar(pool: Pool) -> dict:
    """Populate the governed SSE calendar when a deployment has no rows.

    The read path can safely fall back to persisted official NAV dates, but
    the scheduler still benefits from a complete holiday calendar.  Upsert is
    idempotent, so concurrent cold starts may both fetch without corrupting
    state; subsequent starts are database-only.
    """
    async with system_connection(pool) as conn:
        existing = await conn.fetchval(
            """
            SELECT count(*)
              FROM reference.trading_calendars
             WHERE market = 'SSE'
            """
        )
    if int(existing or 0) > 0:
        return {"status": "SKIPPED", "reason": "CALENDAR_EXISTS", "rows": int(existing)}
    inserted = await fill_trading_calendar(pool)
    return {
        "status": "COMPLETED" if inserted > 0 else "ABSTAINED",
        "reason": None if inserted > 0 else "PROVIDER_UNAVAILABLE",
        "rows": inserted,
    }


async def refresh_market_sentiment(pool: Pool) -> dict:
    """Worker-only Provider refresh; persist provenance before publishing."""
    stats = await md.fetch_market_sentiment()
    if not stats:
        return {"status": "ABSTAINED", "reason": "PROVIDER_UNAVAILABLE"}
    async with system_connection(pool) as conn:
        await repository.upsert_market_sentiment(
            conn, trade_date=stats["trade_date"], data=stats
        )
    return {"status": "COMPLETED", "tradeDate": stats["trade_date"]}


async def _refresh_market_hot_on_connection(conn, *, limit: int) -> dict:
    """Fetch and persist one hotspot snapshot on a Worker connection."""
    snapshot = await md.get_hot_stocks_snapshot(limit=limit)
    snapshot_id = await repository.insert_market_hot_snapshot(conn, data=snapshot)
    return {
        "status": (
            "COMPLETED"
            if snapshot["quality_status"] != md.Q_UNAVAILABLE
            else "ABSTAINED"
        ),
        "snapshotId": snapshot_id,
        "qualityStatus": snapshot["quality_status"],
    }


async def refresh_market_hot(pool: Pool, *, limit: int = 10) -> dict:
    """Worker-only Provider refresh for the market-hot read model."""
    async with system_connection(pool) as conn:
        return await _refresh_market_hot_on_connection(conn, limit=limit)


async def ensure_market_hot_snapshot(pool: Pool, *, limit: int = 10) -> dict:
    """Bootstrap the Worker read model only when no hotspot snapshot exists.

    A newly migrated environment may start on a weekend or outside every
    trading-session catch-up window.  In that state an active Worker would
    otherwise leave the API permanently unavailable until the next scheduled
    session.  The existence check keeps ordinary restarts side-effect free:
    once any governed snapshot exists, the scheduler remains the sole refresh
    cadence and API requests continue to be database-only reads.
    """
    async with system_connection(pool) as conn:
        # The supported Worker DSN is session-pooled because its connection
        # also carries the wealthsail_worker runtime role. A session advisory
        # lock serializes replacement instances without holding a database
        # transaction open while the external Provider responds.
        await conn.execute(
            "SELECT pg_advisory_lock(hashtextextended($1, 0));",
            _MARKET_HOT_BOOTSTRAP_LOCK,
        )
        try:
            # Recheck only after winning the lock: another Worker may have
            # populated the table while this instance was waiting.
            existing = await repository.get_latest_market_hot(conn)
            if existing is not None:
                return {
                    "status": "SKIPPED",
                    "reason": "SNAPSHOT_EXISTS",
                    "qualityStatus": existing["quality_status"],
                }
            return await _refresh_market_hot_on_connection(conn, limit=limit)
        finally:
            await conn.execute(
                "SELECT pg_advisory_unlock(hashtextextended($1, 0));",
                _MARKET_HOT_BOOTSTRAP_LOCK,
            )


async def get_market_hot(conn) -> dict:
    """Return the latest persisted hotspot snapshot without Provider I/O."""
    row = await repository.get_latest_market_hot(conn)
    if row is None:
        return {
            "items": [],
            "source": "unavailable",
            "sourceAsOf": None,
            "retrievedAt": None,
            "qualityStatus": "UNAVAILABLE",
            "methodVersion": None,
            "updatedAt": now_shanghai(),
        }
    quality = str(row["quality_status"])
    retrieved_at = row["retrieved_at"]
    if quality in {
        md.Q_FRESH,
        md.Q_ESTIMATED,
    } and retrieved_at < now_shanghai() - timedelta(minutes=10):
        quality = md.Q_STALE
    return {
        "items": row["items"],
        "source": row["source_name"],
        "sourceAsOf": row["source_as_of"],
        "retrievedAt": retrieved_at,
        "qualityStatus": quality,
        "methodVersion": row["method_version"],
        "updatedAt": row["updated_at"],
    }


async def get_market_sentiment(conn) -> dict:
    """Return the latest governed snapshot without any external I/O."""
    rows = await repository.get_latest_market_sentiment(conn, limit=1)
    if rows:
        r = rows[0]
        persisted_quality = str(r["quality_status"])
        quality = (
            "STALE"
            if persisted_quality == "FRESH" and r["trade_date"] != now_shanghai().date()
            else persisted_quality
        )
        return {
            "tradeDate": r["trade_date"].isoformat(),
            "totalUp": r["total_up"],
            "totalDown": r["total_down"],
            "totalFlat": r["total_flat"],
            "limitUp": r["limit_up"],
            "limitDown": r["limit_down"],
            "turnover": str(r["turnover"]) if r["turnover"] is not None else None,
            "northFlow": str(r["north_flow"]) if r["north_flow"] is not None else None,
            "sentimentScore": (
                str(r["sentiment_score"]) if r["sentiment_score"] is not None else None
            ),
            "source": r["source_name"],
            "sourceAsOf": r["source_as_of"],
            "retrievedAt": r["retrieved_at"],
            "qualityStatus": quality,
            "coveragePct": (
                str(r["coverage_pct"]) if r["coverage_pct"] is not None else None
            ),
            "methodVersion": r["method_version"],
            "updatedAt": r["updated_at"],
        }
    return {
        "tradeDate": None,
        "totalUp": None,
        "totalDown": None,
        "totalFlat": None,
        "limitUp": None,
        "limitDown": None,
        "turnover": None,
        "northFlow": None,
        "sentimentScore": None,
        "source": "unavailable",
        "sourceAsOf": None,
        "retrievedAt": None,
        "qualityStatus": "UNAVAILABLE",
        "coveragePct": None,
        "methodVersion": None,
        "updatedAt": now_shanghai(),
    }

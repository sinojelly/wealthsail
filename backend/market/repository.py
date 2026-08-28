"""Market repository: observation persistence, snapshot recomputation, calendar.

Every financial amount is NUMERIC in the database; Python side stays on
Decimal end-to-end. The recompute path runs inside a per-tenant RLS
transaction so projection rows can only be written for the acting tenant.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import asyncpg


ZERO = Decimal("0.00")


@dataclass
class HoldingProjection:
    account_id: str
    instrument_id: str
    quantity: Decimal = Decimal("0")
    cost_basis: Decimal = Decimal("0")

    @property
    def avg_cost(self) -> Decimal:
        if self.quantity <= 0:
            return Decimal("0.0000")
        return (self.cost_basis / self.quantity).quantize(Decimal("0.0001"))


@dataclass(frozen=True)
class AccountNavValuation:
    total_nav: Decimal | None
    known_value: Decimal
    coverage_pct: Decimal
    unpriced_position_count: int
    quality: str


def derive_account_nav(
    *,
    cash_balance: Decimal,
    principal: Decimal,
    priced_positions_value: Decimal,
    total_position_count: int,
    priced_position_count: int,
) -> AccountNavValuation:
    """Fail closed when a confirmed open position has no valuation."""
    known_value = (cash_balance + priced_positions_value).quantize(Decimal("0.01"))
    unpriced = max(total_position_count - priced_position_count, 0)
    coverage = (
        Decimal("100.00")
        if total_position_count == 0
        else (
            Decimal(priced_position_count)
            / Decimal(total_position_count)
            * Decimal("100")
        ).quantize(Decimal("0.01"))
    )
    return AccountNavValuation(
        total_nav=known_value if unpriced == 0 else None,
        known_value=known_value,
        coverage_pct=coverage,
        unpriced_position_count=unpriced,
        quality="FRESH" if unpriced == 0 else "UNAVAILABLE",
    )


def _json_value(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return []
    return value


def rebuild_confirmed_holdings(
    events: list[asyncpg.Record] | list[dict],
) -> dict[tuple[str, str], HoldingProjection]:
    """Replay already-filtered CONFIRMED event legs into account holdings."""
    holdings: dict[tuple[str, str], HoldingProjection] = {}
    for event in events:
        legs = _json_value(event["legs"])
        if not isinstance(legs, list):
            raise ValueError("ledger event legs must be a JSON array")
        instrument_legs = [leg for leg in legs if leg.get("leg_type") == "INSTRUMENT"]
        for leg in instrument_legs:
            instrument_id = str(leg["instrument_id"])
            account_id = str(event["account_id"])
            key = (account_id, instrument_id)
            state = holdings.setdefault(
                key, HoldingProjection(account_id=account_id, instrument_id=instrument_id)
            )
            quantity = Decimal(str(leg.get("quantity") or "0"))
            if quantity > 0:
                if event["event_type"] != "DIVIDEND_REINVEST":
                    cash_cost = sum(
                        (
                            abs(Decimal(str(part.get("amount") or "0")))
                            for part in legs
                            if part.get("leg_type") in {"CASH", "FEE", "TAX"}
                            and Decimal(str(part.get("amount") or "0")) < 0
                        ),
                        ZERO,
                    )
                    if cash_cost == 0:
                        cash_cost = quantity * Decimal(str(leg.get("unit_price") or "0"))
                    state.cost_basis += cash_cost
                state.quantity += quantity
            elif quantity < 0:
                sold = abs(quantity)
                if sold > state.quantity:
                    raise ValueError("confirmed ledger would create a negative position")
                remaining = state.quantity - sold
                state.cost_basis = (
                    state.cost_basis * remaining / state.quantity
                    if state.quantity > 0
                    else ZERO
                )
                state.quantity = remaining
    return {key: state for key, state in holdings.items() if state.quantity > 0}


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------
async def get_active_instruments(
    conn: asyncpg.Connection,
) -> list[asyncpg.Record]:
    """Active instruments with their TICKER alias (the external lookup key)."""
    return await conn.fetch(
        """
        SELECT i.id AS instrument_id, i.asset_type, i.canonical_name,
               COALESCE(a.alias_value, '') AS ticker
        FROM reference.instruments i
        LEFT JOIN LATERAL (
            SELECT alias_value
            FROM reference.instrument_aliases
            WHERE instrument_id = i.id AND alias_type = 'TICKER'
              AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
            ORDER BY effective_from DESC
            LIMIT 1
        ) a ON TRUE
        WHERE i.lifecycle_status = 'ACTIVE'
        """,
    )


async def get_holdings_for_refresh(
    conn: asyncpg.Connection,
) -> list[asyncpg.Record]:
    """Latest per-instrument holdings across all tenants (for scoped refresh)."""
    return await conn.fetch(
        """
        SELECT DISTINCT ON (ps.tenant_id, ps.instrument_id)
            ps.tenant_id, ps.account_id, ps.instrument_id, ps.quantity,
            ps.avg_cost, ps.data_quality_status
        FROM ledger.position_snapshots ps
        ORDER BY ps.tenant_id, ps.instrument_id, ps.as_of DESC
        """
    )


async def get_source_id(conn: asyncpg.Connection, source_name: str) -> str | None:
    return await conn.fetchval(
        "SELECT id FROM reference.data_sources WHERE name = $1", source_name
    )


# ---------------------------------------------------------------------------
# writes: source snapshots + observations
# ---------------------------------------------------------------------------
async def upsert_data_source(
    conn: asyncpg.Connection, *, name: str, source_type: str
) -> None:
    await conn.execute(
        """
        INSERT INTO reference.data_sources
            (id, name, source_type, license_model, allowed_usage, priority, status)
        VALUES (gen_random_uuid(), $1, $2, 'PUBLIC_API', 'PRICE_OBSERVATION', 80, 'ACTIVE')
        ON CONFLICT (name) DO UPDATE SET status = 'ACTIVE'
        """,
        name,
        source_type,
    )


async def insert_source_snapshot(
    conn: asyncpg.Connection,
    *,
    source_id: str,
    retrieval_key: str,
    observations: list[dict],
    quality_status: str,
    retrieved_at: datetime,
) -> str:
    canonical = json.dumps(observations, sort_keys=True, ensure_ascii=False, default=str)
    content_hash = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return str(
        await conn.fetchval(
            """
            INSERT INTO market.source_snapshots
                (source_id, retrieval_key, origin_uri, as_of, retrieved_at,
                 content_hash, quality_status)
            VALUES ($1, $2, 'internal:worker:market', $3, $4, $5, $6)
            RETURNING id
            """,
            source_id,
            retrieval_key,
            retrieved_at,
            retrieved_at,
            content_hash,
            quality_status,
        )
    )


async def insert_observations(
    conn: asyncpg.Connection,
    rows: list[tuple],
) -> None:
    """Batch-insert normalized observations (UNIQUE conflict → skip)."""
    if not rows:
        return
    await conn.executemany(
        """
        INSERT INTO market.market_observations
            (subject_type, subject_id, field_code, value, lower_value, upper_value,
             unit, currency,
             as_of, observed_at, snapshot_id, source_id, quality_status,
             method_version, metadata, revision)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                $13, $14, $15::jsonb, 1)
        ON CONFLICT (subject_type, subject_id, field_code, as_of, source_id, revision)
        DO NOTHING
        """,
        rows,
    )


async def upsert_fund_disclosures(
    conn: asyncpg.Connection,
    rows: list[tuple],
) -> None:
    """Persist dated source disclosures without converting them into holdings."""
    if not rows:
        return
    await conn.executemany(
        """
        INSERT INTO market.fund_disclosure_components
            (fund_instrument_id, component_code, component_name, weight,
             disclosed_on, disclosure_label, source_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (fund_instrument_id, component_code, disclosed_on)
        DO UPDATE SET component_name=EXCLUDED.component_name,
                      weight=EXCLUDED.weight,
                      disclosure_label=EXCLUDED.disclosure_label,
                      source_id=EXCLUDED.source_id,
                      retrieved_at=now()
        """,
        rows,
    )


async def get_latest_fund_disclosures(
    conn: asyncpg.Connection,
    fund_instrument_ids: list[str],
) -> list[asyncpg.Record]:
    """Read every component from each fund's latest available disclosure."""
    if not fund_instrument_ids:
        return []
    return await conn.fetch(
        """
        SELECT f.fund_instrument_id, f.component_code, f.component_name,
               f.weight, f.disclosed_on, f.disclosure_label
        FROM market.fund_disclosure_components f
        JOIN (
            SELECT fund_instrument_id, max(disclosed_on) AS disclosed_on
            FROM market.fund_disclosure_components
            WHERE fund_instrument_id = ANY($1::uuid[])
            GROUP BY fund_instrument_id
        ) latest USING (fund_instrument_id, disclosed_on)
        ORDER BY f.fund_instrument_id, f.weight DESC
        """,
        fund_instrument_ids,
    )


# ---------------------------------------------------------------------------
# snapshot recomputation (per tenant, inside RLS transaction)
# ---------------------------------------------------------------------------
async def refresh_positions(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    quotes: dict[str, dict],
    as_of_date: date,
) -> int:
    """Rebuild today's projections from CONFIRMED ledger events and quotes."""
    events = await conn.fetch(
        """
        SELECT ev.account_id, ev.event_type,
               jsonb_agg(
                   jsonb_build_object(
                       'leg_type', leg.leg_type,
                       'instrument_id', leg.instrument_id,
                       'quantity', leg.quantity,
                       'amount', leg.amount,
                       'unit_price', leg.unit_price
                   ) ORDER BY leg.id
               ) AS legs
        FROM ledger.ledger_events ev
        JOIN ledger.ledger_legs leg
          ON leg.tenant_id = ev.tenant_id AND leg.event_id = ev.id
        WHERE ev.tenant_id = $1
          AND ev.status = 'CONFIRMED'
          AND ev.trade_date <= $2
          AND ev.event_type <> 'REVERSAL'
          AND NOT EXISTS (
              SELECT 1 FROM ledger.ledger_events reversal
              WHERE reversal.tenant_id = ev.tenant_id
                AND reversal.status = 'CONFIRMED'
                AND reversal.event_type = 'REVERSAL'
                AND reversal.reverses_event_id = ev.id
          )
        GROUP BY ev.id, ev.account_id, ev.event_type, ev.occurred_at
        ORDER BY ev.occurred_at, ev.id
        """,
        tenant_id,
        as_of_date,
    )
    holdings = rebuild_confirmed_holdings(events)
    # Rebuild is deterministic: remove this date's previous projection so
    # closed positions cannot survive a replay.
    await conn.execute(
        "DELETE FROM ledger.position_snapshots WHERE tenant_id = $1 AND as_of = $2",
        tenant_id,
        as_of_date,
    )
    updated = 0
    for holding in holdings.values():
        quote = quotes.get(holding.instrument_id)
        price = quote.get("price") if quote else None
        quality = quote.get("quality", "UNAVAILABLE") if quote else "UNAVAILABLE"
        quantity = holding.quantity
        avg_cost = holding.avg_cost
        market_value = (
            (quantity * price).quantize(Decimal("0.01")) if price is not None else None
        )
        unrealized_pnl = (
            (market_value - quantity * avg_cost).quantize(Decimal("0.01"))
            if market_value is not None
            else None
        )
        await conn.execute(
            """
            INSERT INTO ledger.position_snapshots
                (tenant_id, account_id, instrument_id, as_of, quantity,
                 avg_cost, market_price, market_value, unrealized_pnl,
                 data_quality_status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (tenant_id, account_id, instrument_id, as_of) DO UPDATE
            SET market_price = EXCLUDED.market_price,
                market_value = EXCLUDED.market_value,
                unrealized_pnl = EXCLUDED.unrealized_pnl,
                data_quality_status = EXCLUDED.data_quality_status
            """,
            tenant_id,
            holding.account_id,
            holding.instrument_id,
            as_of_date,
            quantity,
            avg_cost,
            price,
            market_value,
            unrealized_pnl,
            quality,
        )
        updated += 1
    return updated


async def refresh_account_nav(
    conn: asyncpg.Connection,
    *,
    tenant_id: str,
    as_of_date: date,
) -> int:
    """Recompute today's NAV snapshot per account.

    cash_balance: net cash from confirmed CASH legs (ledger is the source of
    truth, not the previous projection);
    principal: net invested capital (CASH_IN − CASH_OUT);
    cumulative_pnl = total_nav − principal;
    daily_pnl     = total_nav − previous trading day total_nav.
    """
    accounts = await conn.fetch(
        """
        SELECT id AS account_id
        FROM identity.accounts
        WHERE tenant_id = $1 AND status = 'ACTIVE'
        """,
        tenant_id,
    )
    updated = 0
    for account in accounts:
        account_id = str(account["account_id"])

        cash_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(leg.amount), 0) AS cash_balance,
                   -- 净投入本金 = CASH_IN 与 CASH_OUT 的净现金流
                   -- （CASH_OUT 的 amount 为负，直接求和即净额）
                   COALESCE(SUM(leg.amount)
                            FILTER (WHERE ev.event_type IN ('CASH_IN', 'CASH_OUT')), 0)
                   AS principal
            FROM ledger.ledger_legs leg
            JOIN ledger.ledger_events ev
              ON ev.tenant_id = leg.tenant_id AND ev.id = leg.event_id
            WHERE leg.tenant_id = $1 AND ev.account_id = $2
              -- Fees and taxes are separate legs but still reduce cash.
              AND leg.amount IS NOT NULL
              AND ev.status = 'CONFIRMED'
              AND ev.event_type <> 'REVERSAL'
              AND NOT EXISTS (
                  -- 与 refresh_positions 保持一致：被 REVERSAL 逆转的事件
                  -- 不再参与现金/本金计算。
                  SELECT 1 FROM ledger.ledger_events reversal
                  WHERE reversal.tenant_id = ev.tenant_id
                    AND reversal.status = 'CONFIRMED'
                    AND reversal.event_type = 'REVERSAL'
                    AND reversal.reverses_event_id = ev.id
              )
            """,
            tenant_id,
            account_id,
        )
        cash_balance = Decimal((cash_row or {}).get("cash_balance") or 0)
        principal = Decimal((cash_row or {}).get("principal") or 0)

        positions_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(market_value) FILTER (WHERE market_value IS NOT NULL), 0)
                   AS positions_value,
                   COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE market_value IS NOT NULL) AS priced_count
            FROM ledger.position_snapshots
            WHERE tenant_id = $1 AND account_id = $2 AND as_of = $3
            """,
            tenant_id,
            account_id,
            as_of_date,
        )
        positions_row = positions_row or {}
        positions_value = Decimal(positions_row.get("positions_value") or 0)
        valuation = derive_account_nav(
            cash_balance=cash_balance,
            principal=principal,
            priced_positions_value=positions_value,
            total_position_count=int(positions_row.get("total") or 0),
            priced_position_count=int(positions_row.get("priced_count") or 0),
        )
        total_nav = valuation.total_nav

        prev_row = await conn.fetchrow(
            """
            SELECT total_nav
            FROM ledger.account_nav_snapshots
            WHERE tenant_id = $1 AND account_id = $2 AND as_of < $3
            ORDER BY as_of DESC
            LIMIT 1
            """,
            tenant_id,
            account_id,
            as_of_date,
        )
        prev_total = (
            Decimal(prev_row["total_nav"])
            if prev_row and prev_row["total_nav"] is not None
            else total_nav
        )
        daily_pnl = (
            (total_nav - prev_total).quantize(Decimal("0.01"))
            if total_nav is not None and prev_total is not None
            else None
        )
        cumulative_pnl = (
            (total_nav - principal).quantize(Decimal("0.01"))
            if total_nav is not None
            else None
        )

        await conn.execute(
            """
            INSERT INTO ledger.account_nav_snapshots
                (tenant_id, account_id, as_of, cash_balance,
                 positions_market_value, total_nav, daily_pnl, cumulative_pnl,
                 data_quality_status, valuation_coverage_pct,
                 unpriced_position_count, known_value)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (tenant_id, account_id, as_of) DO UPDATE
            SET cash_balance = EXCLUDED.cash_balance,
                positions_market_value = EXCLUDED.positions_market_value,
                total_nav = EXCLUDED.total_nav,
                daily_pnl = EXCLUDED.daily_pnl,
                cumulative_pnl = EXCLUDED.cumulative_pnl,
                data_quality_status = EXCLUDED.data_quality_status,
                valuation_coverage_pct = EXCLUDED.valuation_coverage_pct,
                unpriced_position_count = EXCLUDED.unpriced_position_count,
                known_value = EXCLUDED.known_value
            """,
            tenant_id,
            account_id,
            as_of_date,
            cash_balance,
            positions_value if valuation.unpriced_position_count == 0 else None,
            total_nav,
            daily_pnl,
            cumulative_pnl,
            valuation.quality,
            valuation.coverage_pct,
            valuation.unpriced_position_count,
            valuation.known_value,
        )
        updated += 1
    return updated


async def upsert_trading_calendar_rows(
    conn: asyncpg.Connection, rows: list[tuple]
) -> int:
    """Bulk upsert trading-calendar rows (market, calendar_date, is_trading_day)."""
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO reference.trading_calendars (market, calendar_date, is_trading_day, timezone)
        VALUES ('SSE', $1, $2, 'Asia/Shanghai')
        ON CONFLICT (market, calendar_date) DO UPDATE
        SET is_trading_day = EXCLUDED.is_trading_day
        """,
        rows,
    )
    return len(rows)


async def upsert_market_sentiment(
    conn: asyncpg.Connection, *, trade_date: str, data: dict
) -> None:
    """Upsert one day's market-sentiment snapshot (idempotent by trade_date)."""
    await conn.execute(
        """
        INSERT INTO market.market_sentiment
            (trade_date, total_up, total_down, total_flat, limit_up, limit_down,
             turnover, north_flow, sentiment_score, source_name, source_as_of,
             retrieved_at, quality_status, coverage_pct, method_version, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                $14, $15, now())
        ON CONFLICT (trade_date) DO UPDATE SET
            total_up = EXCLUDED.total_up,
            total_down = EXCLUDED.total_down,
            total_flat = EXCLUDED.total_flat,
            limit_up = EXCLUDED.limit_up,
            limit_down = EXCLUDED.limit_down,
            turnover = EXCLUDED.turnover,
            north_flow = EXCLUDED.north_flow,
            sentiment_score = EXCLUDED.sentiment_score,
            source_name = EXCLUDED.source_name,
            source_as_of = EXCLUDED.source_as_of,
            retrieved_at = EXCLUDED.retrieved_at,
            quality_status = EXCLUDED.quality_status,
            coverage_pct = EXCLUDED.coverage_pct,
            method_version = EXCLUDED.method_version,
            updated_at = now()
        """,
        date.fromisoformat(trade_date),
        data.get("total_up"),
        data.get("total_down"),
        data.get("total_flat"),
        data.get("limit_up"),
        data.get("limit_down"),
        data.get("turnover"),
        data.get("north_flow"),
        data.get("sentiment_score"),
        data["source_name"],
        data["source_as_of"],
        data["retrieved_at"],
        data["quality_status"],
        data.get("coverage_pct"),
        data["method_version"],
    )


async def get_latest_market_sentiment(
    conn: asyncpg.Connection, limit: int = 1
) -> list[dict]:
    """Latest persisted sentiment snapshots (newest first)."""
    rows = await conn.fetch(
        """
        SELECT trade_date, total_up, total_down, total_flat, limit_up, limit_down,
               turnover, north_flow, sentiment_score, source_name, source_as_of,
               retrieved_at, quality_status, coverage_pct, method_version, updated_at
        FROM market.market_sentiment
        ORDER BY trade_date DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]

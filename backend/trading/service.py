"""Application services for authenticated trade-fill commands.

The service owns the transaction-level invariants that span a fill, the
immutable ledger and derived portfolio projections. Routes must not call the
repository directly for state transitions.
"""

from __future__ import annotations

from decimal import Decimal

from asyncpg import Connection

from backend.errors import NotFoundError, ValidationError_
from backend.portfolio import service as portfolio_service
from backend.trading.repository import LedgerRepository


async def create_trade_fill_draft(conn: Connection, **command) -> dict:
    return await LedgerRepository().create_trade_fill_draft(conn, **command)


async def list_trade_fills(
    conn: Connection, tenant_id: str, *, limit: int = 50
) -> dict:
    return await LedgerRepository().list_trade_fills(conn, tenant_id, limit=limit)


async def confirm_trade_fill(
    conn: Connection,
    *,
    tenant_id: str,
    fill_id: str,
    idempotency_key: str,
    confirmed_by: str,
    description: str | None = None,
    repository: LedgerRepository | None = None,
) -> dict:
    """Validate and confirm one fill, then rebuild projections atomically."""
    repository = repository or LedgerRepository()
    draft = await conn.fetchrow(
        """
        SELECT id, account_id, instrument_id, side, quantity, unit_price,
               fee, status
        FROM ledger.trade_fills
        WHERE id = $1 AND tenant_id = $2
        FOR UPDATE
        """,
        fill_id,
        tenant_id,
    )
    if draft is None:
        raise NotFoundError("成交草稿不存在")

    if draft["status"] == "DRAFT":
        account_id = str(draft["account_id"])
        instrument_id = str(draft["instrument_id"])
        # Serialise all confirmations for one account. The balance and holding
        # checks below therefore remain valid until this transaction commits.
        locked_account = await conn.fetchval(
            """
            SELECT id
            FROM identity.accounts
            WHERE id = $1 AND tenant_id = $2 AND status = 'ACTIVE'
            FOR UPDATE
            """,
            account_id,
            tenant_id,
        )
        if locked_account is None:
            raise NotFoundError("成交账户不存在或已停用")

        side = str(draft["side"]).upper()
        quantity = Decimal(draft["quantity"])
        if side == "BUY":
            has_exit_rule = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM research.theses
                    WHERE tenant_id = $1
                      AND instrument_id = $2
                      AND status = 'ACTIVE'
                      AND (account_id IS NULL OR account_id = $3)
                      AND jsonb_typeof(invalidation_conditions) = 'array'
                      AND jsonb_array_length(invalidation_conditions) > 0
                )
                """,
                tenant_id,
                instrument_id,
                account_id,
            )
            if not has_exit_rule:
                raise ValidationError_("买入确认前必须维护有效的出局条件")

            cash_balance = Decimal(
                await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(leg.amount), 0)
                    FROM ledger.ledger_events event
                    JOIN ledger.ledger_legs leg
                      ON leg.tenant_id = event.tenant_id
                     AND leg.event_id = event.id
                    WHERE event.tenant_id = $1
                      AND event.account_id = $2
                      AND event.status = 'CONFIRMED'
                      AND event.event_type <> 'REVERSAL'
                      AND leg.amount IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM ledger.ledger_events reversal
                          WHERE reversal.tenant_id = event.tenant_id
                            AND reversal.status = 'CONFIRMED'
                            AND reversal.event_type = 'REVERSAL'
                            AND reversal.reverses_event_id = event.id
                      )
                    """,
                    tenant_id,
                    account_id,
                )
                or 0
            )
            required_cash = (
                quantity * Decimal(draft["unit_price"])
                + Decimal(draft["fee"] or 0)
            )
            if cash_balance < required_cash:
                raise ValidationError_("账户可用现金不足，不能确认该买入成交")
        elif side == "SELL":
            holding = Decimal(
                await conn.fetchval(
                    """
                    SELECT COALESCE(SUM(leg.quantity), 0)
                    FROM ledger.ledger_events event
                    JOIN ledger.ledger_legs leg
                      ON leg.tenant_id = event.tenant_id
                     AND leg.event_id = event.id
                    WHERE event.tenant_id = $1
                      AND event.account_id = $2
                      AND event.status = 'CONFIRMED'
                      AND event.event_type <> 'REVERSAL'
                      AND leg.instrument_id = $3
                      AND leg.quantity IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM ledger.ledger_events reversal
                          WHERE reversal.tenant_id = event.tenant_id
                            AND reversal.status = 'CONFIRMED'
                            AND reversal.event_type = 'REVERSAL'
                            AND reversal.reverses_event_id = event.id
                      )
                    """,
                    tenant_id,
                    account_id,
                    instrument_id,
                )
                or 0
            )
            if holding < quantity:
                raise ValidationError_("持仓不足，不能确认超卖成交")
        else:
            raise ValidationError_("成交方向必须为 BUY 或 SELL")

    result = await repository.confirm_trade_fill(
        conn,
        tenant_id=tenant_id,
        fill_id=fill_id,
        idempotency_key=idempotency_key,
        confirmed_by=confirmed_by,
        description=description,
    )
    if not result["replayed"]:
        await portfolio_service.recompute_projections(conn, tenant_id=tenant_id)
    return result

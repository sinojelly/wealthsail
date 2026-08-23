"""Immutable portfolio-event ledger prototype for WealthSail.

This in-memory domain prototype validates event shapes and rebuilds projections.
Production persistence, durable idempotency, tenant authorization, and atomic
Outbox writes belong to the PostgreSQL repository/application service boundary.
The current leg model is not presented as a general-ledger debit/credit system.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import List, Optional
import uuid


class EventType(str, Enum):
    """Supported ledger event types."""
    CASH_IN = "CASH_IN"
    CASH_OUT = "CASH_OUT"
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND_CASH = "DIVIDEND_CASH"
    DIVIDEND_REINVEST = "DIVIDEND_REINVEST"
    FEE = "FEE"
    TAX = "TAX"
    REVERSAL = "REVERSAL"


class LegType(str, Enum):
    """Types of balance movements carried by a portfolio event."""
    CASH = "CASH"
    INSTRUMENT = "INSTRUMENT"
    FEE = "FEE"
    TAX = "TAX"


@dataclass(frozen=True)
class LedgerLeg:
    """Individual balance movement belonging to a portfolio event.

    Attributes:
        leg_type: Type of the leg (CASH, INSTRUMENT, FEE, TAX).
        currency: Currency code (e.g., 'CNY').
        amount: Cash movement amount (positive for inflow, negative for outflow).
        instrument_id: Unique instrument ID (for INSTRUMENT legs).
        quantity: Unit/share movement quantity (positive for buy, negative for sell).
        unit_price: Unit price at execution.
    """
    leg_type: LegType
    currency: str = "CNY"
    amount: Decimal = Decimal("0.00")
    instrument_id: Optional[uuid.UUID] = None
    quantity: Decimal = Decimal("0.0000")
    unit_price: Decimal = Decimal("0.0000")


@dataclass(frozen=True)
class LedgerEvent:
    """Immutable business transaction event.

    Attributes:
        id: Unique event ID.
        tenant_id: Tenant owner of the event.
        account_id: Account ID.
        event_type: Type of the event.
        trade_date: Trade date.
        idempotency_key: Key ensuring at-most-once execution.
        description: Human-readable note.
        reverses_event_id: Prior event neutralized by this reversal, if any.
        legs: Ordered balance movements that form this portfolio event.
        created_at: System creation timestamp.
    """
    id: uuid.UUID
    tenant_id: uuid.UUID
    account_id: uuid.UUID
    event_type: EventType
    trade_date: date
    idempotency_key: str
    description: str
    reverses_event_id: Optional[uuid.UUID] = None
    legs: tuple[LedgerLeg, ...] = field(default_factory=tuple)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PositionProjection:
    """Derived projection of an instrument holding.

    Attributes:
        instrument_id: Instrument identifier.
        quantity: Total open quantity.
        avg_cost: Diluted average unit cost.
        total_cost_basis: Total cost basis invested.
        accumulated_cash_dividends: Total cash dividends collected.
    """
    instrument_id: uuid.UUID
    quantity: Decimal = Decimal("0.0000")
    avg_cost: Decimal = Decimal("0.0000")
    total_cost_basis: Decimal = Decimal("0.00")
    accumulated_cash_dividends: Decimal = Decimal("0.00")


@dataclass
class PortfolioProjection:
    """Derived portfolio balance projection.

    Attributes:
        tenant_id: Tenant identifier.
        account_id: Account identifier.
        cash_balance: Available cash balance.
        positions: Map of instrument_id to PositionProjection.
    """
    tenant_id: uuid.UUID
    account_id: uuid.UUID
    cash_balance: Decimal = Decimal("0.00")
    positions: dict[uuid.UUID, PositionProjection] = field(default_factory=dict)


class LedgerService:
    """Service managing immutable portfolio events and derived projections."""

    def __init__(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> None:
        """Initializes the ledger service for a specific tenant and account.

        Args:
            tenant_id: Tenant UUID.
            account_id: Account UUID.
        """
        self.tenant_id = tenant_id
        self.account_id = account_id
        self.events: List[LedgerEvent] = []
        self._processed_idempotency_keys: set[str] = set()

    def record_cash_in(
        self,
        amount: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "资金入账",
    ) -> LedgerEvent:
        """Records capital deposit.

        Args:
            amount: Deposit amount (must be positive).
            trade_date: Transaction date.
            idempotency_key: Unique idempotency key.
            description: Transaction memo.

        Returns:
            Recorded LedgerEvent.

        Raises:
            ValueError: If amount is non-positive or idempotency key is reused.
        """
        if amount <= Decimal("0.00"):
            raise ValueError("入金金额必须大于 0")
        if idempotency_key in self._processed_idempotency_keys:
            raise ValueError(f"重复提交：幂等键 {idempotency_key} 已存在")

        event = LedgerEvent(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            account_id=self.account_id,
            event_type=EventType.CASH_IN,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            description=description,
            legs=(
                LedgerLeg(
                    leg_type=LegType.CASH,
                    currency="CNY",
                    amount=amount,
                ),
            ),
        )
        self.events.append(event)
        self._processed_idempotency_keys.add(idempotency_key)
        return event

    def record_buy(
        self,
        instrument_id: uuid.UUID,
        quantity: Decimal,
        unit_price: Decimal,
        fee: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "买入成交",
    ) -> LedgerEvent:
        """Records a security/fund purchase.

        Args:
            instrument_id: Instrument UUID.
            quantity: Quantity bought (must be positive).
            unit_price: Execution unit price (must be positive).
            fee: Brokerage fee and transaction tax.
            trade_date: Trade date.
            idempotency_key: Unique idempotency key.
            description: Transaction memo.

        Returns:
            Recorded LedgerEvent.

        Raises:
            ValueError: On invalid inputs or insufficient cash.
        """
        if quantity <= Decimal("0.0000") or unit_price <= Decimal("0.0000"):
            raise ValueError("买入数量和单价必须大于 0")
        if fee < Decimal("0.00"):
            raise ValueError("手续费不能为负数")
        if idempotency_key in self._processed_idempotency_keys:
            raise ValueError(f"重复提交：幂等键 {idempotency_key} 已存在")

        total_cash_required = (quantity * unit_price) + fee
        current_portfolio = self.rebuild_projection()
        if current_portfolio.cash_balance < total_cash_required:
            raise ValueError(
                f"可用现金不足：需要 {total_cash_required} 元，当前可用 {current_portfolio.cash_balance} 元"
            )

        legs = [
            LedgerLeg(
                leg_type=LegType.INSTRUMENT,
                instrument_id=instrument_id,
                quantity=quantity,
                unit_price=unit_price,
            ),
            LedgerLeg(
                leg_type=LegType.CASH,
                currency="CNY",
                amount=-(quantity * unit_price),
            ),
        ]
        if fee > Decimal("0.00"):
            legs.append(
                LedgerLeg(
                    leg_type=LegType.FEE,
                    currency="CNY",
                    amount=-fee,
                )
            )

        event = LedgerEvent(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            account_id=self.account_id,
            event_type=EventType.BUY,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            description=description,
            legs=tuple(legs),
        )
        self.events.append(event)
        self._processed_idempotency_keys.add(idempotency_key)
        return event

    def record_cash_out(
        self,
        amount: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "资金出账",
    ) -> LedgerEvent:
        """Records a cash withdrawal after checking the current projection."""
        if amount <= Decimal("0.00"):
            raise ValueError("出金金额必须大于 0")
        if idempotency_key in self._processed_idempotency_keys:
            raise ValueError(f"重复提交：幂等键 {idempotency_key} 已存在")
        if self.rebuild_projection().cash_balance < amount:
            raise ValueError("可用现金不足，无法出金")

        event = LedgerEvent(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            account_id=self.account_id,
            event_type=EventType.CASH_OUT,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            description=description,
            legs=(LedgerLeg(leg_type=LegType.CASH, amount=-amount),),
        )
        self.events.append(event)
        self._processed_idempotency_keys.add(idempotency_key)
        return event

    def record_fee(
        self,
        amount: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "独立费用",
    ) -> LedgerEvent:
        """Records a standalone cash fee not already included in a fill."""
        return self._record_cash_charge(
            EventType.FEE,
            LegType.FEE,
            amount,
            trade_date,
            idempotency_key,
            description,
        )

    def record_tax(
        self,
        amount: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "独立税费",
    ) -> LedgerEvent:
        """Records a standalone cash tax not already included in a fill."""
        return self._record_cash_charge(
            EventType.TAX,
            LegType.TAX,
            amount,
            trade_date,
            idempotency_key,
            description,
        )

    def _record_cash_charge(
        self,
        event_type: EventType,
        leg_type: LegType,
        amount: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str,
    ) -> LedgerEvent:
        """Validates and appends a standalone fee or tax event."""
        if amount <= Decimal("0.00"):
            raise ValueError("费用或税额必须大于 0")
        if idempotency_key in self._processed_idempotency_keys:
            raise ValueError(f"重复提交：幂等键 {idempotency_key} 已存在")
        if self.rebuild_projection().cash_balance < amount:
            raise ValueError("可用现金不足，无法扣除费用或税额")

        event = LedgerEvent(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            account_id=self.account_id,
            event_type=event_type,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            description=description,
            legs=(LedgerLeg(leg_type=leg_type, amount=-amount),),
        )
        self.events.append(event)
        self._processed_idempotency_keys.add(idempotency_key)
        return event

    def record_sell(
        self,
        instrument_id: uuid.UUID,
        quantity: Decimal,
        unit_price: Decimal,
        fee: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "卖出成交",
    ) -> LedgerEvent:
        """Records a user-confirmed fill that reduces an existing position.

        Advice acceptance alone must never call this method. The application
        service calls it only after receiving a broker fill, an accepted import,
        or a separately confirmed manual-fill draft.
        """
        if quantity <= Decimal("0.0000") or unit_price <= Decimal("0.0000"):
            raise ValueError("卖出数量和单价必须大于 0")
        if fee < Decimal("0.00"):
            raise ValueError("手续费不能为负数")
        if idempotency_key in self._processed_idempotency_keys:
            raise ValueError(f"重复提交：幂等键 {idempotency_key} 已存在")

        current_portfolio = self.rebuild_projection()
        position = current_portfolio.positions.get(instrument_id)
        if position is None or position.quantity < quantity:
            available = position.quantity if position else Decimal("0.0000")
            raise ValueError(f"持仓不足：计划卖出 {quantity}，当前持有 {available}")

        legs = [
            LedgerLeg(
                leg_type=LegType.INSTRUMENT,
                instrument_id=instrument_id,
                quantity=-quantity,
                unit_price=unit_price,
            ),
            LedgerLeg(
                leg_type=LegType.CASH,
                currency="CNY",
                amount=quantity * unit_price,
            ),
        ]
        if fee > Decimal("0.00"):
            legs.append(LedgerLeg(leg_type=LegType.FEE, currency="CNY", amount=-fee))

        event = LedgerEvent(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            account_id=self.account_id,
            event_type=EventType.SELL,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            description=description,
            legs=tuple(legs),
        )
        self.events.append(event)
        self._processed_idempotency_keys.add(idempotency_key)
        return event

    def record_fund_dividend_cash(
        self,
        instrument_id: uuid.UUID,
        cash_per_share: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "基金现金分红",
    ) -> LedgerEvent:
        """Records cash dividend payout.

        Args:
            instrument_id: Instrument UUID.
            cash_per_share: Dividend per share (must be positive).
            trade_date: Ex-dividend / payment date.
            idempotency_key: Unique idempotency key.
            description: Transaction memo.

        Returns:
            Recorded LedgerEvent.

        Raises:
            ValueError: If position is empty or cash per share is non-positive.
        """
        if cash_per_share <= Decimal("0.0000"):
            raise ValueError("每份分红金额必须大于 0")
        if idempotency_key in self._processed_idempotency_keys:
            raise ValueError(f"重复提交：幂等键 {idempotency_key} 已存在")

        current_portfolio = self.rebuild_projection()
        pos = current_portfolio.positions.get(instrument_id)
        if not pos or pos.quantity <= Decimal("0.0000"):
            raise ValueError("无持仓份额，无法执行分红")

        total_dividend_cash = (pos.quantity * cash_per_share).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        event = LedgerEvent(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            account_id=self.account_id,
            event_type=EventType.DIVIDEND_CASH,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            description=description,
            legs=(
                LedgerLeg(
                    leg_type=LegType.CASH,
                    currency="CNY",
                    amount=total_dividend_cash,
                ),
            ),
        )
        self.events.append(event)
        self._processed_idempotency_keys.add(idempotency_key)
        return event

    def record_fund_dividend_reinvest(
        self,
        instrument_id: uuid.UUID,
        cash_per_share: Decimal,
        reinvest_nav: Decimal,
        trade_date: date,
        idempotency_key: str,
        description: str = "基金红利再投资",
    ) -> LedgerEvent:
        """Records dividend reinvestment, increasing shares and diluting unit cost.

        Args:
            instrument_id: Instrument UUID.
            cash_per_share: Dividend per share.
            reinvest_nav: Ex-dividend unit NAV used for share conversion.
            trade_date: Ex-dividend date.
            idempotency_key: Unique idempotency key.
            description: Transaction memo.

        Returns:
            Recorded LedgerEvent.

        Raises:
            ValueError: If reinvestment NAV is invalid or position is empty.
        """
        if reinvest_nav <= Decimal("0.0000") or cash_per_share <= Decimal("0.0000"):
            raise ValueError("分红金额与折算净值必须大于 0")
        if idempotency_key in self._processed_idempotency_keys:
            raise ValueError(f"重复提交：幂等键 {idempotency_key} 已存在")

        current_portfolio = self.rebuild_projection()
        pos = current_portfolio.positions.get(instrument_id)
        if not pos or pos.quantity <= Decimal("0.0000"):
            raise ValueError("无持仓份额，无法执行红利再投资")

        total_dividend = (pos.quantity * cash_per_share).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        new_shares = (total_dividend / reinvest_nav).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )

        event = LedgerEvent(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            account_id=self.account_id,
            event_type=EventType.DIVIDEND_REINVEST,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            description=description,
            legs=(
                LedgerLeg(
                    leg_type=LegType.INSTRUMENT,
                    instrument_id=instrument_id,
                    quantity=new_shares,
                    unit_price=reinvest_nav,
                ),
            ),
        )
        self.events.append(event)
        self._processed_idempotency_keys.add(idempotency_key)
        return event

    def record_reversal(
        self,
        target_event_id: uuid.UUID,
        trade_date: date,
        idempotency_key: str,
        description: str = "事件冲正",
    ) -> LedgerEvent:
        """Neutralizes the latest unreversed economic event without rewriting it.

        This in-memory prototype only reverses the latest unreversed event. That
        restriction keeps subsequent events from depending on history that the
        reversal removes. The persistent domain service may support older-event
        corrections by appending a validated compensating sequence.
        """
        if idempotency_key in self._processed_idempotency_keys:
            raise ValueError(f"重复提交：幂等键 {idempotency_key} 已存在")

        reversed_ids = {
            event.reverses_event_id
            for event in self.events
            if event.event_type is EventType.REVERSAL and event.reverses_event_id is not None
        }
        candidates = [
            event
            for event in self.events
            if event.event_type is not EventType.REVERSAL and event.id not in reversed_ids
        ]
        if not candidates or candidates[-1].id != target_event_id:
            raise ValueError("原型仅允许冲正最新经济事件")
        target = candidates[-1]

        inverse_legs = tuple(
            LedgerLeg(
                leg_type=leg.leg_type,
                currency=leg.currency,
                amount=-leg.amount,
                instrument_id=leg.instrument_id,
                quantity=-leg.quantity,
                unit_price=leg.unit_price,
            )
            for leg in target.legs
        )
        event = LedgerEvent(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            account_id=self.account_id,
            event_type=EventType.REVERSAL,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            description=description,
            reverses_event_id=target.id,
            legs=inverse_legs,
        )
        self.events.append(event)
        self._processed_idempotency_keys.add(idempotency_key)
        return event

    def rebuild_projection(self) -> PortfolioProjection:
        """Rebuilds the entire portfolio and position projections from immutable events.

        Returns:
            Up-to-date PortfolioProjection.
        """
        projection = PortfolioProjection(
            tenant_id=self.tenant_id,
            account_id=self.account_id,
        )

        reversed_event_ids = {
            event.reverses_event_id
            for event in self.events
            if event.event_type is EventType.REVERSAL and event.reverses_event_id is not None
        }

        for event in self.events:
            if event.event_type is EventType.REVERSAL or event.id in reversed_event_ids:
                continue
            if event.event_type == EventType.CASH_IN:
                for leg in event.legs:
                    if leg.leg_type == LegType.CASH:
                        projection.cash_balance += leg.amount

            elif event.event_type == EventType.CASH_OUT:
                for leg in event.legs:
                    if leg.leg_type == LegType.CASH:
                        projection.cash_balance += leg.amount

            elif event.event_type == EventType.BUY:
                inst_leg = next(l for l in event.legs if l.leg_type == LegType.INSTRUMENT)
                cash_leg = next(l for l in event.legs if l.leg_type == LegType.CASH)
                fee_leg = next((l for l in event.legs if l.leg_type == LegType.FEE), None)

                inst_id = inst_leg.instrument_id
                assert inst_id is not None
                if inst_id not in projection.positions:
                    projection.positions[inst_id] = PositionProjection(instrument_id=inst_id)

                pos = projection.positions[inst_id]
                old_cost_basis = pos.total_cost_basis
                old_qty = pos.quantity

                added_cost = abs(cash_leg.amount) + (abs(fee_leg.amount) if fee_leg else Decimal("0.00"))
                new_qty = old_qty + inst_leg.quantity
                new_cost_basis = old_cost_basis + added_cost
                new_avg_cost = (new_cost_basis / new_qty).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

                pos.quantity = new_qty
                pos.total_cost_basis = new_cost_basis
                pos.avg_cost = new_avg_cost

                # Update cash
                projection.cash_balance += cash_leg.amount
                if fee_leg:
                    projection.cash_balance += fee_leg.amount

            elif event.event_type == EventType.SELL:
                inst_leg = next(l for l in event.legs if l.leg_type == LegType.INSTRUMENT)
                cash_leg = next(l for l in event.legs if l.leg_type == LegType.CASH)
                fee_leg = next((l for l in event.legs if l.leg_type == LegType.FEE), None)

                inst_id = inst_leg.instrument_id
                assert inst_id is not None
                pos = projection.positions[inst_id]
                sold_quantity = abs(inst_leg.quantity)
                old_quantity = pos.quantity
                remaining_quantity = old_quantity - sold_quantity
                remaining_ratio = remaining_quantity / old_quantity

                pos.quantity = remaining_quantity
                pos.total_cost_basis = (pos.total_cost_basis * remaining_ratio).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                pos.avg_cost = (
                    (pos.total_cost_basis / remaining_quantity).quantize(
                        Decimal("0.0001"), rounding=ROUND_HALF_UP
                    )
                    if remaining_quantity > Decimal("0.0000")
                    else Decimal("0.0000")
                )

                projection.cash_balance += cash_leg.amount
                if fee_leg:
                    projection.cash_balance += fee_leg.amount

            elif event.event_type == EventType.DIVIDEND_CASH:
                for leg in event.legs:
                    if leg.leg_type == LegType.CASH:
                        projection.cash_balance += leg.amount

            elif event.event_type == EventType.DIVIDEND_REINVEST:
                inst_leg = next(l for l in event.legs if l.leg_type == LegType.INSTRUMENT)
                inst_id = inst_leg.instrument_id
                assert inst_id is not None
                pos = projection.positions[inst_id]

                # Dividend reinvestment increases shares while keeping total invested cost basis constant,
                # thus strictly diluting the unit cost per share.
                new_qty = pos.quantity + inst_leg.quantity
                pos.quantity = new_qty
                pos.avg_cost = (pos.total_cost_basis / new_qty).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )

            elif event.event_type in (EventType.FEE, EventType.TAX):
                projection.cash_balance += sum(
                    (leg.amount for leg in event.legs), Decimal("0.00")
                )

        return projection

"""成交与不可变账本持久化仓储。

将 :mod:`modules.ledger.service` 的内存领域原型落到 PostgreSQL 不可变账本：
``ledger.ledger_events`` / ``ledger.ledger_legs``，并实现"成交"实体
（``ledger.trade_fills``）的 DRAFT → CONFIRMED 生命周期——只有独立确认的
成交才能关联 ``ledger_event_id``（建议采纳只写 reporting.decisions，
见 modules/research/service.py，三者严格分离）。

所有写操作要求调用方已处于租户 RLS 事务内（传入 conn），幂等键
``UNIQUE (tenant_id, idempotency_key)`` 保证 at-most-once。
"""

from __future__ import annotations

import logging
import json
from datetime import date
from decimal import Decimal
from typing import Any

from asyncpg import Connection as AsyncConnection

from backend.errors import (
    IdempotencyConflictError as ConflictError,
    NotFoundError,
    ValidationError_ as ValidationError,
    VersionConflictError,
)

logger = logging.getLogger("wealthsail.trading.repository")

ZERO = Decimal("0.00")

# 与 003/007 迁移 CHECK 对齐
EVENT_TYPES = {
    "CASH_IN", "CASH_OUT", "BUY", "SELL", "DIVIDEND_CASH",
    "DIVIDEND_REINVEST", "FEE", "TAX", "SPLIT", "MERGER",
    "TRANSFER_IN", "TRANSFER_OUT", "ADJUSTMENT", "REVERSAL",
}
LEG_TYPES = {"CASH", "INSTRUMENT", "FEE", "TAX"}
FILL_SIDES = {"BUY", "SELL"}
FILL_STATUSES = {"DRAFT", "CONFIRMED", "REJECTED", "CANCELLED"}


def _d(value: object) -> Decimal:
    """规整为 Decimal，绝不经过 float。"""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        return Decimal(value)
    raise TypeError(f"unexpected numeric type: {type(value).__name__}")


def _leg_signature(leg: tuple | dict) -> tuple:
    """Return a stable, Decimal-safe signature for an economic leg."""
    if isinstance(leg, dict):
        values = (
            leg["leg_type"],
            leg["instrument_id"],
            leg["quantity"],
            leg["amount"],
            leg["unit_price"],
        )
    else:
        values = leg
    leg_type, instrument_id, quantity, amount, unit_price = values
    return (
        leg_type,
        str(instrument_id) if instrument_id is not None else None,
        _d(quantity) if quantity is not None else None,
        _d(amount) if amount is not None else None,
        _d(unit_price) if unit_price is not None else None,
    )


class LedgerRepository:
    """不可变账本与成交记录的持久化仓储。"""

    # -- 事件持久化 ----------------------------------------------------------
    async def persist_event(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        account_id: str,
        event_type: str,
        trade_date: date,
        idempotency_key: str,
        description: str,
        legs: list[tuple[str, str | None, Decimal | None, Decimal | None, Decimal | None]],
        reverses_event_id: str | None = None,
        status: str = "CONFIRMED",
    ) -> dict[str, Any]:
        """写入一条不可变账本事件及其腿（幂等：同键返回既有事件）。

        Args:
            conn: 调用方租户事务内的连接。
            tenant_id / account_id: 事件归属。
            event_type: 003 迁移 CHECK 允许的事件类型。
            trade_date: 交易日。
            idempotency_key: 幂等键（tenant 内唯一）。
            description: 事件备注。
            legs: (leg_type, instrument_id, quantity, amount, unit_price) 元组列表。
            reverses_event_id: REVERSAL 事件的目标事件 id。
            status: PENDING / CONFIRMED / REVERSED。

        Returns:
            {"eventId": str, "replayed": bool}。
        """
        if event_type not in EVENT_TYPES:
            raise ValidationError(f"unsupported ledger event type: {event_type}")
        if status not in {"PENDING", "CONFIRMED", "REVERSED"}:
            raise ValidationError(f"invalid ledger status: {status}")
        for leg in legs:
            if leg[0] not in LEG_TYPES:
                raise ValidationError(f"unsupported ledger leg type: {leg[0]}")

        event_id = await conn.fetchval(
            """
            INSERT INTO ledger.ledger_events
                (tenant_id, account_id, event_type, status, trade_date,
                 idempotency_key, description, reverses_event_id)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING id
            """,
            tenant_id,
            account_id,
            event_type,
            status,
            trade_date,
            idempotency_key,
            description,
            reverses_event_id,
        )
        if event_id is not None:
            rows = [
                (
                    tenant_id,
                    event_id,
                    leg_type,
                    instrument_id,
                    quantity,
                    amount,
                    unit_price,
                )
                for leg_type, instrument_id, quantity, amount, unit_price in legs
            ]
            if rows:
                await conn.executemany(
                    """
                    INSERT INTO ledger.ledger_legs
                        (tenant_id, event_id, leg_type, instrument_id,
                         quantity, amount, unit_price)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    rows,
                )
            logger.info(
                "账本事件已写入: tenant=%s type=%s event=%s",
                tenant_id, event_type, event_id,
            )
            return {"eventId": str(event_id), "replayed": False}

        # 幂等重放：返回既有事件（不重复记账）
        existing = await conn.fetchrow(
            """
            SELECT id, account_id, event_type, status, trade_date,
                   description, reverses_event_id
            FROM ledger.ledger_events
            WHERE tenant_id = $1 AND idempotency_key = $2
            """,
            tenant_id,
            idempotency_key,
        )
        if existing is None:  # 理论上不可达（同事务内冲突行必可见）
            raise RuntimeError("ledger idempotency conflict without an existing row")
        existing_legs = await conn.fetch(
            """
            SELECT leg_type, instrument_id, quantity, amount, unit_price
            FROM ledger.ledger_legs
            WHERE tenant_id = $1 AND event_id = $2
            ORDER BY id
            """,
            tenant_id,
            existing["id"],
        )
        same_command = (
            str(existing["account_id"]) == str(account_id)
            and existing["event_type"] == event_type
            and existing["status"] == status
            and existing["trade_date"] == trade_date
            and existing["description"] == description
            and (
                str(existing["reverses_event_id"])
                if existing["reverses_event_id"] is not None
                else None
            )
            == (str(reverses_event_id) if reverses_event_id is not None else None)
            and [_leg_signature(leg) for leg in existing_legs]
            == [_leg_signature(leg) for leg in legs]
        )
        if not same_command:
            raise ConflictError("Idempotency-Key 已用于另一笔账本命令")
        logger.info("账本事件幂等重放: tenant=%s event=%s", tenant_id, existing["id"])
        return {"eventId": str(existing["id"]), "replayed": True}

    # -- 成交（trade_fills）---------------------------------------------------
    async def record_trade(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        account_id: str,
        instrument_id: str,
        side: str,
        quantity: Decimal,
        unit_price: Decimal,
        trade_date: date,
        idempotency_key: str,
        created_by: str,
        fee: Decimal = ZERO,
        decision_id: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """记录一笔独立确认的成交：写 BUY/SELL 账本事件 + CONFIRMED 成交行。

        这是"成交"实体的唯一落账路径：调用方必须已经获得独立确认（用户
        确认/对账导入/券商回报），建议采纳本身不会走到这里。

        Returns:
            {"eventId", "fillId", "replayed"}。
        """
        side = side.upper()
        if side not in FILL_SIDES:
            raise ValueError(f"trade side must be BUY or SELL, got {side}")
        quantity = _d(quantity)
        unit_price = _d(unit_price)
        fee = _d(fee)
        if quantity <= 0:
            raise ValueError("成交数量必须大于 0")
        if unit_price <= 0:
            raise ValueError("成交单价必须大于 0")
        if fee < 0:
            raise ValueError("手续费不能为负")

        # 账本事件（事件类型即 side；腿方向随 side 取反）
        instrument_leg_qty = quantity if side == "BUY" else -quantity
        cash_leg_amount = (
            -(quantity * unit_price) if side == "BUY" else (quantity * unit_price)
        )
        legs: list[tuple] = [
            ("INSTRUMENT", instrument_id, instrument_leg_qty, None, unit_price),
            ("CASH", None, None, cash_leg_amount, None),
        ]
        if fee > 0:
            legs.append(("FEE", None, None, -fee, None))

        persisted = await self.persist_event(
            conn,
            tenant_id=tenant_id,
            account_id=account_id,
            event_type=side,
            trade_date=trade_date,
            idempotency_key=idempotency_key,
            description=description or f"独立确认成交 {side}",
            legs=legs,
        )
        event_id = persisted["eventId"]

        fill_id = await conn.fetchval(
            """
            INSERT INTO ledger.trade_fills
                (tenant_id, account_id, instrument_id, decision_id, side,
                 quantity, unit_price, fee, trade_date, occurred_at,
                 source_type, status, ledger_event_id, created_by, idempotency_key,
                 confirmed_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now(),
                    'USER_CONFIRMED', 'CONFIRMED', $10, $11, $12, now())
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING id
            """,
            tenant_id,
            account_id,
            instrument_id,
            decision_id,
            side,
            quantity,
            unit_price,
            fee,
            trade_date,
            event_id,
            created_by,
            idempotency_key,
        )
        if fill_id is None:
            existing = await conn.fetchrow(
                """
                SELECT id FROM ledger.trade_fills
                WHERE tenant_id = $1 AND idempotency_key = $2
                """,
                tenant_id,
                idempotency_key,
            )
            fill_id = existing["id"] if existing is not None else None

        logger.info(
            "成交已确认: tenant=%s side=%s event=%s fill=%s",
            tenant_id, side, event_id, fill_id,
        )
        return {
            "eventId": event_id,
            "fillId": str(fill_id) if fill_id is not None else None,
            "replayed": persisted["replayed"],
        }

    async def create_trade_fill_draft(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        account_id: str,
        instrument_id: str,
        side: str,
        quantity: Decimal,
        unit_price: Decimal,
        trade_date: date,
        idempotency_key: str,
        created_by: str,
        fee: Decimal = ZERO,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        """创建成交草稿（DRAFT，无账本事件；确认前不产生任何经济事实）。"""
        side = side.upper()
        if side not in FILL_SIDES:
            raise ValueError(f"trade side must be BUY or SELL, got {side}")
        quantity = _d(quantity)
        unit_price = _d(unit_price)
        fee = _d(fee)
        if quantity <= 0 or unit_price <= 0:
            raise ValueError("成交数量与单价必须大于 0")

        fill_id = await conn.fetchval(
            """
            INSERT INTO ledger.trade_fills
                (tenant_id, account_id, instrument_id, decision_id, side,
                 quantity, unit_price, fee, trade_date, occurred_at,
                 source_type, status, created_by, idempotency_key)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now(),
                    'USER_CONFIRMED', 'DRAFT', $10, $11)
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING id
            """,
            tenant_id,
            account_id,
            instrument_id,
            decision_id,
            side,
            quantity,
            unit_price,
            fee,
            trade_date,
            created_by,
            idempotency_key,
        )
        if fill_id is None:
            existing = await conn.fetchrow(
                """
                SELECT id, account_id, instrument_id, decision_id, side,
                       quantity, unit_price, fee, trade_date, created_by, status
                FROM ledger.trade_fills
                WHERE tenant_id = $1 AND idempotency_key = $2
                """,
                tenant_id,
                idempotency_key,
            )
            if existing is None:
                raise RuntimeError("trade fill idempotency conflict without a row")
            same_command = (
                str(existing["account_id"]) == str(account_id)
                and str(existing["instrument_id"]) == str(instrument_id)
                and (
                    str(existing["decision_id"])
                    if existing["decision_id"] is not None
                    else None
                )
                == (str(decision_id) if decision_id is not None else None)
                and existing["side"] == side
                and _d(existing["quantity"]) == quantity
                and _d(existing["unit_price"]) == unit_price
                and _d(existing["fee"]) == fee
                and existing["trade_date"] == trade_date
                and str(existing["created_by"]) == str(created_by)
            )
            if not same_command:
                raise ConflictError("Idempotency-Key 已用于另一笔成交草稿")
            return {"fillId": str(existing["id"]), "status": existing["status"], "replayed": True}

        await conn.execute(
            """
            INSERT INTO audit.audit_log
                (tenant_id, actor_type, actor_id, action, resource_type,
                 resource_id, details)
            VALUES ($1, 'USER', $2, 'TRADE_FILL_DRAFT_CREATED',
                    'ledger.trade_fills', $3, $4)
            """,
            tenant_id,
            created_by,
            str(fill_id),
            json.dumps(
                {"side": side, "tradeDate": trade_date.isoformat()},
                ensure_ascii=False,
            ),
        )
        logger.info("成交草稿已创建: tenant=%s fill=%s", tenant_id, fill_id)
        return {"fillId": str(fill_id), "status": "DRAFT", "replayed": False}

    async def confirm_trade_fill(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        fill_id: str,
        idempotency_key: str,
        confirmed_by: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """确认成交草稿：写账本事件并回填 ledger_event_id（原子，同一事务）。"""
        draft = await conn.fetchrow(
            """
            SELECT f.id, f.account_id, f.instrument_id, f.side, f.quantity,
                   f.unit_price, f.fee, f.trade_date, f.decision_id,
                   f.status, f.ledger_event_id, e.idempotency_key AS event_key
            FROM ledger.trade_fills f
            LEFT JOIN ledger.ledger_events e ON e.id = f.ledger_event_id
            WHERE f.id = $1 AND f.tenant_id = $2
            FOR UPDATE OF f
            """,
            fill_id,
            tenant_id,
        )
        if draft is None:
            raise NotFoundError("成交草稿不存在")
        if draft["status"] == "CONFIRMED":
            if draft["event_key"] != idempotency_key:
                raise VersionConflictError("成交已经使用另一幂等键确认")
            return {
                "fillId": str(draft["id"]),
                "status": "CONFIRMED",
                "eventId": str(draft["ledger_event_id"]),
                "replayed": True,
            }
        if draft["status"] != "DRAFT":
            raise VersionConflictError("当前成交状态不允许确认")

        side = str(draft["side"]).upper()
        instrument_leg_qty = _d(draft["quantity"]) if side == "BUY" else -_d(draft["quantity"])
        cash_leg_amount = (
            -(_d(draft["quantity"]) * _d(draft["unit_price"]))
            if side == "BUY"
            else (_d(draft["quantity"]) * _d(draft["unit_price"]))
        )
        legs: list[tuple] = [
            ("INSTRUMENT", str(draft["instrument_id"]), instrument_leg_qty, None, _d(draft["unit_price"])),
            ("CASH", None, None, cash_leg_amount, None),
        ]
        if _d(draft["fee"]) > 0:
            legs.append(("FEE", None, None, -_d(draft["fee"]), None))

        persisted = await self.persist_event(
            conn,
            tenant_id=tenant_id,
            account_id=str(draft["account_id"]),
            event_type=side,
            trade_date=draft["trade_date"],
            idempotency_key=idempotency_key,
            # Always bind the event payload to this fill, even when the user
            # supplied an optional note. This prevents two drafts from sharing
            # one idempotent economic event.
            description=(
                f"确认成交草稿 {fill_id}：{description.strip()}"
                if description and description.strip()
                else f"确认成交草稿 {fill_id}"
            ),
            legs=legs,
        )
        event_id = persisted["eventId"]

        updated = await conn.execute(
            """
            UPDATE ledger.trade_fills
               SET status = 'CONFIRMED', ledger_event_id = $1,
                   confirmed_at = now(), confirmed_by = $4
             WHERE id = $2 AND tenant_id = $3 AND status = 'DRAFT'
            """,
            event_id,
            fill_id,
            tenant_id,
            confirmed_by,
        )
        if not updated.endswith("1"):
            raise RuntimeError("trade fill confirm raced with another transaction")
        await conn.execute(
            """
            INSERT INTO audit.audit_log
                (tenant_id, actor_type, actor_id, action, resource_type,
                 resource_id, details)
            VALUES ($1, 'USER', $2, 'TRADE_FILL_CONFIRMED',
                    'ledger.trade_fills', $3, $4)
            """,
            tenant_id,
            confirmed_by,
            fill_id,
            json.dumps({"ledgerEventId": event_id}, ensure_ascii=False),
        )

        logger.info("成交草稿已确认: tenant=%s fill=%s event=%s", tenant_id, fill_id, event_id)
        return {
            "fillId": fill_id,
            "status": "CONFIRMED",
            "eventId": event_id,
            "replayed": persisted["replayed"],
        }

    async def list_trade_fills(
        self, conn: AsyncConnection, tenant_id: str, *, limit: int = 50
    ) -> dict:
        """最近成交列表（含标的与账本事件关联）。"""
        rows = await conn.fetch(
            """
            SELECT f.id, f.side, f.quantity, f.unit_price, f.fee, f.trade_date,
                   f.source_type, f.status, f.ledger_event_id, f.created_at,
                   f.confirmed_at, i.canonical_name,
                   COALESCE(a.alias_value, '') AS ticker
            FROM ledger.trade_fills f
            JOIN reference.instruments i ON i.id = f.instrument_id
            LEFT JOIN LATERAL (
                SELECT alias_value
                FROM reference.instrument_aliases
                WHERE instrument_id = f.instrument_id
                  AND alias_type = 'TICKER'
                  AND (effective_to IS NULL OR effective_to >= f.trade_date)
                ORDER BY effective_from DESC
                LIMIT 1
            ) a ON TRUE
            WHERE f.tenant_id = $1
            ORDER BY f.created_at DESC
            LIMIT $2
            """,
            tenant_id,
            limit,
        )
        return {
            "items": [
                {
                    "fillId": str(r["id"]),
                    "symbol": r["ticker"],
                    "name": r["canonical_name"],
                    "side": r["side"],
                    "quantity": str(_d(r["quantity"])),
                    "unitPrice": str(_d(r["unit_price"])),
                    "fee": str(_d(r["fee"])),
                    "tradeDate": r["trade_date"].isoformat(),
                    "sourceType": r["source_type"],
                    "status": r["status"],
                    "eventId": str(r["ledger_event_id"]) if r["ledger_event_id"] else None,
                    "confirmedAt": r["confirmed_at"],
                }
                for r in rows
            ],
            "total": len(rows),
        }

"""建议采纳决策领域服务（modules/research/service.py）。

实现"建议 → 决策 → 成交"分离的第一环：用户对投研建议的响应只写
``reporting.decisions``（ACCEPTED / IGNORED / DEFERRED，幂等），**不**产生
任何账本事实。只有当用户独立确认成交时，modules/ledger/repository.py 的
``record_trade`` / ``confirm_trade_fill`` 才会写入 ``ledger.trade_fills`` 并
关联账本事件——决策与成交通过 ``decision_id`` 弱关联（ON DELETE SET NULL），
互不阻塞。
"""

from __future__ import annotations

import logging
from typing import Any

from modules.database import AsyncConnection

logger = logging.getLogger("wealthsail.modules.research")

# 与 007 迁移 CHECK 对齐
DECISION_ACTIONS = {"ACCEPTED", "IGNORED", "DEFERRED"}
DECISION_STATUSES = {"RECORDED", "RETRACTED", "SUPERSEDED"}


class DecisionService:
    """投研建议采纳决策（reporting.decisions）服务。"""

    async def record_decision(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        user_id: str,
        decision_action: str,
        idempotency_key: str,
        report_id: str | None = None,
        recommendation_id: str | None = None,
        reason: str | None = None,
        correction: str | None = None,
        previous_decision_id: str | None = None,
    ) -> dict[str, Any]:
        """记录用户对建议的决策（RECORDED，幂等）。

        Args:
            conn: 调用方租户事务内的连接。
            tenant_id / user_id: 决策归属与决策人。
            decision_action: ACCEPTED（采纳）/ IGNORED（忽略）/ DEFERRED（推迟）。
            idempotency_key: 幂等键（tenant 内唯一）。
            report_id / recommendation_id: 关联的报告与建议（可选）。
            reason: 决策理由（可选）。
            correction: 修正说明（DEFERRED 时常用，可选）。
            previous_decision_id: 被本次决策取代的上一决策（可选）。

        Returns:
            {"decisionId", "replayed"}。

        Raises:
            ValueError: 决策动作非法或幂等键缺失。
        """
        action = (decision_action or "").strip().upper()
        if action not in DECISION_ACTIONS:
            raise ValueError(f"decision_action 必须为 {'/'.join(sorted(DECISION_ACTIONS))}")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("Idempotency-Key 必填且不能超过 128 个字符")

        decision_id = await conn.fetchval(
            """
            INSERT INTO reporting.decisions
                (tenant_id, report_id, recommendation_id, user_id,
                 decision_action, status, reason, correction, idempotency_key,
                 previous_decision_id)
            VALUES ($1, $2, $3, $4, $5, 'RECORDED', $6, $7, $8, $9)
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            RETURNING id
            """,
            tenant_id,
            report_id,
            recommendation_id,
            user_id,
            action,
            reason,
            correction,
            idempotency_key,
            previous_decision_id,
        )
        if decision_id is not None:
            logger.info(
                "决策已记录: tenant=%s action=%s decision=%s",
                tenant_id, action, decision_id,
            )
            return {"decisionId": str(decision_id), "replayed": False}

        existing = await conn.fetchrow(
            """
            SELECT id, decision_action, status FROM reporting.decisions
            WHERE tenant_id = $1 AND idempotency_key = $2
            """,
            tenant_id,
            idempotency_key,
        )
        if existing is None:
            raise RuntimeError("decision idempotency conflict without a row")
        logger.info("决策幂等重放: tenant=%s decision=%s", tenant_id, existing["id"])
        return {
            "decisionId": str(existing["id"]),
            "decisionAction": existing["decision_action"],
            "status": existing["status"],
            "replayed": True,
        }

    async def retract_decision(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        decision_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """撤回一条已记录决策（RECORDED → RETRACTED，软删保留审计）。"""
        row = await conn.fetchrow(
            """
            UPDATE reporting.decisions
               SET status = 'RETRACTED', reason = COALESCE($1, reason)
             WHERE id = $2 AND tenant_id = $3 AND status = 'RECORDED'
            RETURNING id, status
            """,
            reason,
            decision_id,
            tenant_id,
        )
        if row is None:
            return {"decisionId": decision_id, "status": "NOT_FOUND_OR_NOT_RECORDED"}
        logger.info("决策已撤回: tenant=%s decision=%s", tenant_id, decision_id)
        return {"decisionId": str(row["id"]), "status": row["status"]}

    async def list_decisions(
        self, conn: AsyncConnection, tenant_id: str, *, limit: int = 50
    ) -> dict:
        """最近决策列表（含关联报告/建议摘要）。"""
        rows = await conn.fetch(
            """
            SELECT d.id, d.decision_action, d.status, d.reason, d.correction,
                   d.decided_at, d.report_id, d.recommendation_id,
                   r.summary AS recommendation_summary,
                   COALESCE(i.canonical_name, '') AS instrument_name,
                   COALESCE(a.alias_value, '') AS ticker
            FROM reporting.decisions d
            LEFT JOIN research.recommendations r ON r.id = d.recommendation_id
            LEFT JOIN reference.instruments i ON i.id = r.instrument_id
            LEFT JOIN LATERAL (
                SELECT alias_value
                FROM reference.instrument_aliases
                WHERE instrument_id = i.id
                  AND alias_type = 'TICKER'
                  AND (effective_to IS NULL OR effective_to >= d.decided_at::date)
                ORDER BY effective_from DESC
                LIMIT 1
            ) a ON TRUE
            WHERE d.tenant_id = $1
            ORDER BY d.decided_at DESC
            LIMIT $2
            """,
            tenant_id,
            limit,
        )
        return {
            "items": [
                {
                    "decisionId": str(r["id"]),
                    "decisionAction": r["decision_action"],
                    "status": r["status"],
                    "reason": r["reason"],
                    "correction": r["correction"],
                    "decidedAt": r["decided_at"],
                    "reportId": str(r["report_id"]) if r["report_id"] else None,
                    "recommendationId": str(r["recommendation_id"]) if r["recommendation_id"] else None,
                    "instrument": (
                        f"{r['ticker']} {r['instrument_name']}".strip()
                        if r["ticker"] or r["instrument_name"]
                        else None
                    ),
                }
                for r in rows
            ],
            "total": len(rows),
        }

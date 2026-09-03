"""往 market.fund_lookthrough 回填一只场外基金的持仓快照（迁移角色）。

这是迁移级运维操作（表只对 API/worker 授 SELECT，写入需 owner 登录），
不在应用端点开放。

用法（仓库根目录）：
    export MIGRATION_DATABASE_URL=postgres://owner:...@host:port/db
    python scripts/ingest_fund_holdings.py \
        --fund-symbol 110011 --name "易方达消费行业" \
        --period 2026-06-30 \
        --holdings "600519:8.50:贵州茅台,000858:7.20:五粮液,601318:6.10:中国平安"

参数说明：
    --fund-symbol  基金 6 位代码（TICKER 别名解析）
    --name         基金中文名；仅当 reference.instruments 尚无该基金时需要
    --period       YYYY-MM-DD 报告期（季报披露日）
    --holdings     "代码:权重:名称,代码:权重:名称,..."（名称可省略，
                   仅用于尚未注册持仓股票时自动建 instrument 行）
    --dry-run      只解析与预检，不写库

同一 (fund, holding, report_period) 已存在时用最新 as_of 覆盖权重（幂等重放
财报回填），不产生历史噪音；返回本批 upsert 计数与已存在的明细。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID

import asyncpg

_CODE_RE = re.compile(r"^[0-9]{6}$")


@dataclass(frozen=True)
class Holding:
    symbol: str
    weight: str
    name: str = ""

    def __post_init__(self) -> None:
        if not _CODE_RE.match(self.symbol):
            raise ValueError(f"持仓代码必须是 6 位数字: {self.symbol!r}")


@dataclass
class IngestPlan:
    fund_symbol: str
    fund_name: str
    period: date
    holdings: list[Holding] = field(default_factory=list)


def parse_holdings(raw: str) -> list[Holding]:
    """解析 'code:weight[:name],...' → Holding 列表（权重允许小数）。"""
    holdings: list[Holding] = []
    for segment in raw.split(","):
        segment = segment.strip()
        if not segment:
            continue
        parts = segment.split(":")
        if len(parts) < 2 or len(parts) > 3:
            raise ValueError(f"无法解析持仓项: {segment!r}（期望 code:weight[:name]）")
        symbol = parts[0].strip()
        weight = parts[1].strip()
        try:
            numeric = float(weight)
        except ValueError as exc:
            raise ValueError(f"权重非数值: {weight!r}") from exc
        if not 0 <= numeric <= 100:
            raise ValueError(f"权重须在 0~100 之间: {weight!r}")
        name = parts[2].strip() if len(parts) == 3 else ""
        holdings.append(Holding(symbol=symbol, weight=weight, name=name))
    if not holdings:
        raise ValueError("至少需要一项持仓")
    return holdings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fund-symbol", required=True, help="基金 6 位代码")
    parser.add_argument("--period", required=True, help="报告期 YYYY-MM-DD")
    parser.add_argument(
        "--holdings", required=True, help='持仓 "code:weight[:name],..."'
    )
    parser.add_argument("--name", default="", help="基金中文名")
    parser.add_argument("--dry-run", action="store_true", help="只预检不写库")
    return parser


async def _resolve_fund(
    conn, code: str, fund_name: str
) -> tuple[UUID | None, str | None]:
    """按 TICKER alias 解析基金 instrument_id；缺行则返回 None 与建议名。"""
    row = await conn.fetchrow(
        """
        SELECT instrument.id, instrument.canonical_name
          FROM reference.instruments instrument
          JOIN reference.instrument_aliases alias
            ON alias.instrument_id = instrument.id
         WHERE alias.alias_type = 'TICKER'
           AND alias.alias_value = $1
           AND alias.effective_from <= CURRENT_DATE
           AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
         LIMIT 1
        """,
        code,
    )
    if row is not None:
        return UUID(str(row["id"])), str(row["canonical_name"])
    canonical = f"{code} {fund_name}".strip() if fund_name else ""
    return None, canonical or None


async def _resolve_holding(conn, holding: Holding) -> UUID:
    row = await conn.fetchrow(
        """
        SELECT instrument.id
          FROM reference.instruments instrument
          JOIN reference.instrument_aliases alias
            ON alias.instrument_id = instrument.id
         WHERE alias.alias_type = 'TICKER'
           AND alias.alias_value = $1
           AND alias.effective_from <= CURRENT_DATE
           AND (alias.effective_to IS NULL OR alias.effective_to >= CURRENT_DATE)
         LIMIT 1
        """,
        holding.symbol,
    )
    if row is not None:
        return UUID(str(row["id"]))
    # 自动注册 A 股持仓（与 049 登记风格一致：CN_STOCK/SSE + TICKER alias）。
    name = holding.name or holding.symbol
    created = await conn.fetchrow(
        """
        INSERT INTO reference.instruments (
            asset_type, primary_market, trading_currency, canonical_name,
            lifecycle_status, metadata
        )
        VALUES (
            'CN_STOCK', 'SSE', 'CNY', $1, 'ACTIVE',
            jsonb_build_object('registeredBy', 'holdings-ingest', 'source', 'fund-report')
        )
        RETURNING id
        """,
        name,
    )
    instrument_id = UUID(str(created["id"]))
    await conn.execute(
        """
        INSERT INTO reference.instrument_aliases (
            instrument_id, alias_type, alias_value, market, effective_from
        )
        VALUES ($1, 'TICKER', $2, 'SSE', CURRENT_DATE)
        ON CONFLICT DO NOTHING
        """,
        instrument_id,
        holding.symbol,
    )
    return instrument_id


async def _upsert_lookthrough(
    conn,
    fund_id: UUID,
    holding_id: UUID,
    weight: str,
    period: date,
) -> None:
    await conn.execute(
        """
        INSERT INTO market.fund_lookthrough (
            fund_instrument_id, holding_instrument_id, weight_pct,
            report_period, as_of
        )
        VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (fund_instrument_id, holding_instrument_id, report_period)
        DO UPDATE SET weight_pct = EXCLUDED.weight_pct, as_of = now()
        """,
        fund_id,
        holding_id,
        weight,
        period,
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    period = date.fromisoformat(args.period)
    holdings = parse_holdings(args.holdings)
    dsn = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit(
            "MIGRATION_DATABASE_URL 必填（owner/迁移角色连接串，含 host 与 database）"
        )
    conn = await asyncpg.connect(dsn)
    try:
        fund_id, canonical = await _resolve_fund(conn, args.fund_symbol, args.name)
        if fund_id is None:
            raise SystemExit(
                f"基金 {args.fund_symbol} 在 reference.instruments 中不存在；"
                "请先跑迁移 052 注册或提供 --name 使其自动登记。"
            )
        results: dict[str, Any] = {
            "fund": {"symbol": args.fund_symbol, "name": canonical},
            "period": period.isoformat(),
            "dryRun": args.dry_run,
            "upserted": 0,
            "existing": [],
            "autoRegistered": [],
        }
        for holding in holdings:
            holding_id = await _resolve_holding(conn, holding)
            if args.dry_run:
                results["existing"].append(holding.symbol)
                continue
            status = await conn.fetchval(
                """
                SELECT weight_pct
                  FROM market.fund_lookthrough
                 WHERE fund_instrument_id = $1
                   AND holding_instrument_id = $2
                   AND report_period = $3
                """,
                fund_id,
                holding_id,
                period,
            )
            await _upsert_lookthrough(conn, fund_id, holding_id, holding.weight, period)
            if status is None:
                results["upserted"] += 1
            else:
                results["existing"].append(
                    {"symbol": holding.symbol, "previousWeight": str(status)}
                )
        return results
    finally:
        await conn.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fund-symbol", required=True, help="基金 6 位代码")
    parser.add_argument("--period", required=True, help="报告期 YYYY-MM-DD")
    parser.add_argument(
        "--holdings", required=True, help='持仓 "code:weight[:name],..."'
    )
    parser.add_argument("--name", default="", help="基金中文名")
    parser.add_argument("--dry-run", action="store_true", help="只预检不写库")
    return parser


def main() -> None:
    result = asyncio.run(run(_parser().parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

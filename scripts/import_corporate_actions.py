"""Validate and append corporate-action revisions from a UTF-8 CSV file.

The command is intentionally migration-owner only.  It never creates instrument
master data and defaults to a read-only dry run; pass ``--apply`` to persist.

Example:
  python scripts/import_corporate_actions.py \
      --file docs/examples/corporate-actions.example.csv \
      --confirm-target db.example:5432/postgres
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg

from scripts.migrate import safe_target_name


ACTION_TYPE_ALIASES = {
    "EX_RIGHTS": "EX_RIGHTS",
    "除权除息": "EX_RIGHTS",
    "CASH_DIVIDEND": "CASH_DIVIDEND",
    "分红派息": "CASH_DIVIDEND",
    "STOCK_DIVIDEND": "STOCK_DIVIDEND",
    "送股": "STOCK_DIVIDEND",
    "SPLIT": "SPLIT",
    "拆股": "SPLIT",
    "RIGHTS_ISSUE": "RIGHTS_ISSUE",
    "配股": "RIGHTS_ISSUE",
    "MERGER": "MERGER",
    "合并": "MERGER",
    "CODE_CHANGE": "CODE_CHANGE",
    "代码变更": "CODE_CHANGE",
    "DELISTING": "DELISTING",
    "退市": "DELISTING",
    "OTHER": "OTHER",
    "其他重大公司行动": "OTHER",
}
VALID_MARKETS = frozenset({"SSE", "SZSE", "BSE", "FUND_CN", "SGE"})
REQUIRED_COLUMNS = frozenset(
    {"symbol", "market", "action_type", "action_date", "summary"}
)


@dataclass(frozen=True)
class CorporateActionInput:
    line_number: int
    symbol: str
    market: str
    asset_name: str
    action_type: str
    action_date: date
    summary: str
    ex_date: date | None
    record_date: date | None
    pay_date: date | None
    source_event_id: str
    revision: int
    source_ref: str | None


def _date_value(raw: object, *, line_number: int, field: str) -> date | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"第 {line_number} 行 {field} 必须使用 YYYY-MM-DD"
        ) from exc


def _source_event_id(values: dict[str, object]) -> str:
    supplied = str(values.get("source_event_id") or "").strip()
    if supplied:
        normalized = supplied if supplied.startswith("manual-csv:") else f"manual-csv:{supplied}"
    else:
        identity = "|".join(
            str(values.get(key) or "").strip()
            for key in (
                "symbol",
                "market",
                "action_type",
                "action_date",
                "ex_date",
                "record_date",
                "pay_date",
                "summary",
            )
        )
        normalized = "manual-csv:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    if len(normalized) > 200:
        raise ValueError("source_event_id 不能超过 200 个字符")
    return normalized


def _parse_row(raw: dict[str, object], line_number: int) -> CorporateActionInput:
    symbol = str(raw.get("symbol") or "").strip().upper()
    market = str(raw.get("market") or "").strip().upper()
    action_raw = str(raw.get("action_type") or "").strip()
    summary = str(raw.get("summary") or "").strip()
    if not symbol:
        raise ValueError(f"第 {line_number} 行 symbol 不能为空")
    if market not in VALID_MARKETS:
        raise ValueError(f"第 {line_number} 行 market 不受支持: {market or '空'}")
    action_type = ACTION_TYPE_ALIASES.get(action_raw.upper()) or ACTION_TYPE_ALIASES.get(
        action_raw
    )
    if action_type is None:
        raise ValueError(f"第 {line_number} 行 action_type 不受支持: {action_raw or '空'}")
    action_date = _date_value(
        raw.get("action_date"), line_number=line_number, field="action_date"
    )
    if action_date is None:
        raise ValueError(f"第 {line_number} 行 action_date 不能为空")
    if not summary or len(summary) > 500:
        raise ValueError(f"第 {line_number} 行 summary 长度必须为 1-500")

    ex_date = _date_value(raw.get("ex_date"), line_number=line_number, field="ex_date")
    record_date = _date_value(
        raw.get("record_date"), line_number=line_number, field="record_date"
    )
    pay_date = _date_value(
        raw.get("pay_date"), line_number=line_number, field="pay_date"
    )
    if action_type == "EX_RIGHTS" and ex_date is None:
        ex_date = action_date
    if action_type == "CASH_DIVIDEND" and pay_date is None:
        pay_date = action_date

    revision_text = str(raw.get("revision") or "1").strip()
    try:
        revision = int(revision_text)
    except ValueError as exc:
        raise ValueError(f"第 {line_number} 行 revision 必须为正整数") from exc
    if revision <= 0:
        raise ValueError(f"第 {line_number} 行 revision 必须为正整数")

    normalized_for_id = {
        **raw,
        "symbol": symbol,
        "market": market,
        "action_type": action_type,
        "action_date": action_date.isoformat(),
        "ex_date": ex_date.isoformat() if ex_date else "",
        "record_date": record_date.isoformat() if record_date else "",
        "pay_date": pay_date.isoformat() if pay_date else "",
        "summary": summary,
    }
    return CorporateActionInput(
        line_number=line_number,
        symbol=symbol,
        market=market,
        asset_name=str(raw.get("asset_name") or "").strip(),
        action_type=action_type,
        action_date=action_date,
        summary=summary,
        ex_date=ex_date,
        record_date=record_date,
        pay_date=pay_date,
        source_event_id=_source_event_id(normalized_for_id),
        revision=revision,
        source_ref=str(raw.get("source_ref") or "").strip() or None,
    )


def parse_csv(path: Path) -> list[CorporateActionInput]:
    """Parse and validate the complete file before any database transaction."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = {str(column or "").strip() for column in (reader.fieldnames or [])}
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError("CSV 缺少必填列: " + ", ".join(missing))
        rows = [_parse_row(dict(row), index) for index, row in enumerate(reader, start=2)]
    if not rows:
        raise ValueError("CSV 没有数据行")
    seen: set[tuple[str, int]] = set()
    for row in rows:
        key = (row.source_event_id, row.revision)
        if key in seen:
            raise ValueError(
                f"第 {row.line_number} 行存在重复的 source_event_id/revision: "
                f"{row.source_event_id}/{row.revision}"
            )
        seen.add(key)
    return rows


async def _resolve_instrument(conn, row: CorporateActionInput) -> tuple[str, str]:
    matches = await conn.fetch(
        """
        SELECT instrument.id, instrument.canonical_name
          FROM reference.instrument_aliases alias
          JOIN reference.instruments instrument ON instrument.id = alias.instrument_id
         WHERE alias.alias_type = 'TICKER'
           AND upper(alias.alias_value) = $1
           AND upper(alias.market) = $2
           AND alias.effective_from <= $3
           AND (alias.effective_to IS NULL OR alias.effective_to >= $3)
         ORDER BY instrument.id
        """,
        row.symbol,
        row.market,
        row.action_date,
    )
    if not matches:
        raise ValueError(
            f"第 {row.line_number} 行 {row.symbol}/{row.market} 未匹配到已有标的"
        )
    if len(matches) != 1:
        raise ValueError(
            f"第 {row.line_number} 行 {row.symbol}/{row.market} 匹配到 {len(matches)} 个标的"
        )
    return str(matches[0]["id"]), str(matches[0]["canonical_name"])


async def ingest_actions(
    conn, rows: list[CorporateActionInput], *, apply: bool
) -> dict[str, int | bool]:
    """Resolve every row, then append new revisions when the write gate is open."""
    source_id = await conn.fetchval(
        """
        SELECT id
          FROM reference.data_sources
         WHERE name = 'MANUAL_CORPORATE_ACTIONS'
           AND source_type = 'MARKET_DATA'
           AND status = 'ACTIVE'
        """
    )
    if source_id is None:
        raise RuntimeError("MANUAL_CORPORATE_ACTIONS 数据源不存在，请先执行 migration 057")

    resolved: list[tuple[CorporateActionInput, str, str]] = []
    for row in rows:
        instrument_id, canonical_name = await _resolve_instrument(conn, row)
        resolved.append((row, instrument_id, canonical_name))

    if not apply:
        return {"validated": len(resolved), "inserted": 0, "skipped": 0, "dryRun": True}

    inserted = 0
    skipped = 0
    for row, instrument_id, canonical_name in resolved:
        action_id = await conn.fetchval(
            """
            INSERT INTO market.corporate_actions (
                instrument_id, action_type, action_date, ex_date, record_date,
                pay_date, params, source_id, summary, source_event_id, revision,
                source_ref
            )
            VALUES (
                $1::uuid, $2, $3, $4, $5, $6, $7::jsonb, $8::uuid, $9, $10,
                $11, $12
            )
            ON CONFLICT (source_event_id, revision) DO NOTHING
            RETURNING id
            """,
            instrument_id,
            row.action_type,
            row.action_date,
            row.ex_date,
            row.record_date,
            row.pay_date,
            json.dumps(
                {
                    "importMethod": "CSV",
                    "inputAssetName": row.asset_name or None,
                    "canonicalName": canonical_name,
                },
                ensure_ascii=False,
            ),
            source_id,
            row.summary,
            row.source_event_id,
            row.revision,
            row.source_ref,
        )
        if action_id is None:
            skipped += 1
        else:
            inserted += 1
    return {
        "validated": len(resolved),
        "inserted": inserted,
        "skipped": skipped,
        "dryRun": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path, help="UTF-8 CSV 文件")
    parser.add_argument(
        "--confirm-target",
        required=True,
        help="确认目标 host:port/database；必须与连接串完全一致",
    )
    parser.add_argument("--apply", action="store_true", help="通过校验后写入；默认仅预检")
    return parser


async def run(args: argparse.Namespace) -> dict[str, Any]:
    rows = parse_csv(args.file)
    dsn = os.environ.get("MIGRATION_DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit("MIGRATION_DATABASE_URL 必填，且必须使用迁移/owner 连接串")
    target = safe_target_name(dsn)
    if args.confirm_target != target:
        raise SystemExit(
            f"目标确认不匹配：实际为 {target}，请核对后重新传入 --confirm-target"
        )
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            result = await ingest_actions(conn, rows, apply=bool(args.apply))
    finally:
        await conn.close()
    return {"target": target, "file": str(args.file), **result}


def main() -> None:
    result = asyncio.run(run(_parser().parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

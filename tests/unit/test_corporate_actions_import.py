"""Behavior tests for the migration-owner corporate-action CSV command."""

from __future__ import annotations

import importlib
from datetime import date
from pathlib import Path

import pytest


def _command():
    try:
        return importlib.import_module("scripts.import_corporate_actions")
    except ModuleNotFoundError:
        pytest.fail("scripts.import_corporate_actions is not implemented")


def test_parse_csv_accepts_bom_and_normalizes_four_supported_action_names(
    tmp_path: Path,
) -> None:
    command = _command()
    path = tmp_path / "actions.csv"
    path.write_text(
        "symbol,market,asset_name,action_type,action_date,summary\n"
        "600036,SSE,招商银行,除权除息,2026-09-08,今日除权除息\n"
        "600519,SSE,贵州茅台,分红派息,2026-09-09,现金红利到账\n"
        "000001,SZSE,平安银行,配股,2026-09-10,配股缴款开始\n"
        "300750,SZSE,宁德时代,其他重大公司行动,2026-09-11,重大资产事项\n",
        encoding="utf-8-sig",
    )

    rows = command.parse_csv(path)

    assert [row.action_type for row in rows] == [
        "EX_RIGHTS",
        "CASH_DIVIDEND",
        "RIGHTS_ISSUE",
        "OTHER",
    ]
    assert rows[0].ex_date == date(2026, 9, 8)
    assert rows[1].pay_date == date(2026, 9, 9)
    assert all(row.source_event_id.startswith("manual-csv:") for row in rows)


def test_parse_csv_rejects_invalid_date_and_unknown_action(tmp_path: Path) -> None:
    command = _command()
    bad_date = tmp_path / "bad-date.csv"
    bad_date.write_text(
        "symbol,market,action_type,action_date,summary\n"
        "600036,SSE,除权除息,09/08/2026,日期错误\n",
        encoding="utf-8",
    )
    bad_action = tmp_path / "bad-action.csv"
    bad_action.write_text(
        "symbol,market,action_type,action_date,summary\n"
        "600036,SSE,传闻,2026-09-08,未知类型\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="第 2 行.*action_date"):
        command.parse_csv(bad_date)
    with pytest.raises(ValueError, match="第 2 行.*action_type"):
        command.parse_csv(bad_action)


def test_parse_csv_generates_stable_id_and_rejects_duplicate_revision(
    tmp_path: Path,
) -> None:
    command = _command()
    path = tmp_path / "duplicate.csv"
    header = "symbol,market,action_type,action_date,summary,revision\n"
    row = "600036,SSE,EX_RIGHTS,2026-09-08,今日除权,1\n"
    path.write_text(header + row, encoding="utf-8")
    first = command.parse_csv(path)[0]
    second = command.parse_csv(path)[0]
    assert first.source_event_id == second.source_event_id

    path.write_text(header + row + row, encoding="utf-8")
    with pytest.raises(ValueError, match="重复的 source_event_id/revision"):
        command.parse_csv(path)


class _Connection:
    def __init__(self, *, matches: list[dict], existing: set[tuple[str, int]] | None = None):
        self.matches = matches
        self.existing = set(existing or set())
        self.inserted: list[tuple[str, int]] = []

    async def fetch(self, query: str, *_args):
        if "reference.instrument_aliases" in query:
            return self.matches
        raise AssertionError(query)

    async def fetchval(self, query: str, *args):
        if "reference.data_sources" in query:
            return "source-manual"
        if "INSERT INTO market.corporate_actions" in query:
            key = (str(args[9]), int(args[10]))
            if key in self.existing:
                return None
            self.existing.add(key)
            self.inserted.append(key)
            return f"action-{len(self.inserted)}"
        raise AssertionError(query)


def _row(command):
    return command.CorporateActionInput(
        line_number=2,
        symbol="600036",
        market="SSE",
        asset_name="招商银行",
        action_type="EX_RIGHTS",
        action_date=date(2026, 9, 8),
        summary="今日除权除息",
        ex_date=date(2026, 9, 8),
        record_date=None,
        pay_date=None,
        source_event_id="manual-csv:event-1",
        revision=1,
        source_ref=None,
    )


@pytest.mark.asyncio
async def test_ingest_rejects_ambiguous_symbol_market_mapping() -> None:
    command = _command()
    conn = _Connection(
        matches=[
            {"id": "instrument-1", "canonical_name": "招商银行"},
            {"id": "instrument-2", "canonical_name": "招商银行旧代码"},
        ]
    )

    with pytest.raises(ValueError, match="第 2 行.*匹配到 2 个标的"):
        await command.ingest_actions(conn, [_row(command)], apply=False)


@pytest.mark.asyncio
async def test_dry_run_validates_without_writing() -> None:
    command = _command()
    conn = _Connection(matches=[{"id": "instrument-1", "canonical_name": "招商银行"}])

    result = await command.ingest_actions(conn, [_row(command)], apply=False)

    assert result == {"validated": 1, "inserted": 0, "skipped": 0, "dryRun": True}
    assert conn.inserted == []


@pytest.mark.asyncio
async def test_apply_is_idempotent_and_reports_duplicate_skip() -> None:
    command = _command()
    row = _row(command)
    conn = _Connection(matches=[{"id": "instrument-1", "canonical_name": "招商银行"}])

    first = await command.ingest_actions(conn, [row], apply=True)
    second = await command.ingest_actions(conn, [row], apply=True)

    assert first == {"validated": 1, "inserted": 1, "skipped": 0, "dryRun": False}
    assert second == {"validated": 1, "inserted": 0, "skipped": 1, "dryRun": False}
    assert conn.inserted == [("manual-csv:event-1", 1)]

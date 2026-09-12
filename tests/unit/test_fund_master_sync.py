"""Contracts for the weekly market-wide fund master synchronizer."""

from __future__ import annotations


import pandas as pd
import pytest

from backend.integrations.fund_master import _entries_from_frame
from backend.jobs.fund_master_sync import (
    lookup_fund_master_identity,
    master_first_identity_resolver,
    upsert_fund_master_entries,
)


def _sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"基金代码": "000001", "拼音缩写": "HXCZHH", "基金简称": "华夏成长混合", "基金类型": "混合型-灵活", "拼音全称": "HUAXIA"},
            {"基金代码": "000002", "拼音缩写": "HXCZHH", "基金简称": "华夏成长混合(后端)", "基金类型": "混合型-灵活", "拼音全称": "HUAXIA"},
            {"基金代码": "12", "拼音缩写": "BAD", "基金简称": "坏行", "基金类型": "混合型", "拼音全称": "BAD"},
            {"基金代码": "000001", "拼音缩写": "DUP", "基金简称": "重复代码", "基金类型": "混合型", "拼音全称": "DUP"},
            {"基金代码": None, "拼音缩写": None, "基金简称": None, "基金类型": None, "拼音全称": None},
        ]
    )


def test_entries_from_frame_requires_six_digit_codes_and_deduplicates() -> None:
    """Short/missing codes drop; duplicate codes keep the first occurrence."""

    entries = _entries_from_frame(_sample_frame())

    assert [entry.fund_code for entry in entries] == ["000001", "000002"]
    assert entries[0].fund_name == "华夏成长混合"
    assert entries[0].pinyin_abbr == "HXCZHH"


def test_entries_from_frame_rejects_empty_response() -> None:
    with pytest.raises(RuntimeError):
        _entries_from_frame(pd.DataFrame())


class _UpsertConnection:
    def __init__(self) -> None:
        self.rows: list[tuple] = []
        self.queries: list[str] = []

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def executemany(self, query: str, rows):
        self.queries.append(query)
        self.rows = list(rows)
        return len(rows)


@pytest.mark.asyncio
async def test_upsert_entries_sends_every_entry_through_the_function() -> None:
    conn = _UpsertConnection()
    entries = _entries_from_frame(_sample_frame())

    upserted = await upsert_fund_master_entries(conn, entries)

    assert upserted == 2
    assert [row[0] for row in conn.rows] == ["000001", "000002"]
    assert any("upsert_fund_master" in query for query in conn.queries)


class _IdentityConnection:
    def __init__(self, row) -> None:
        self.row = row

    def __enter__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def fetchrow(self, query: str, *_args):
        if "FROM reference.fund_master" in query:
            return self.row
        raise AssertionError(f"unexpected query: {query}")


@pytest.mark.asyncio
async def test_lookup_fund_master_identity_returns_master_fast_path() -> None:
    conn = _IdentityConnection(
        {"fund_name": "银华富裕主题混合A", "fund_type": "混合型-偏股"}
    )

    identity = await lookup_fund_master_identity(conn, "180012")

    assert identity == ("银华富裕主题混合A", "混合型-偏股")


@pytest.mark.asyncio
async def test_master_first_resolver_falls_back_to_provider_on_miss() -> None:
    conn = _IdentityConnection(None)

    async def provider_identity(fund_code: str):
        return (f"来自源{fund_code}", "OFF_EXCHANGE_FUND")

    def conn_factory(_fund_code: str):
        return conn

    resolver = master_first_identity_resolver(conn_factory, provider_identity)
    identity = await resolver("999999")

    assert identity == ("来自源999999", "OFF_EXCHANGE_FUND")


@pytest.mark.asyncio
async def test_master_first_resolver_uses_master_without_provider_call() -> None:
    conn = _IdentityConnection(
        {"fund_name": "银华富裕主题混合A", "fund_type": "混合型-偏股"}
    )

    async def provider_identity(fund_code: str):  # pragma: no cover
        raise AssertionError("provider identity must not be called on a hit")

    def conn_factory(_fund_code: str):
        return conn

    resolver = master_first_identity_resolver(conn_factory, provider_identity)
    identity = await resolver("180012")

    assert identity == ("银华富裕主题混合A", "混合型-偏股")

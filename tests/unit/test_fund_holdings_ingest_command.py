"""Contracts for scripts/ingest_fund_holdings.py helpers (no DB I/O)."""

from __future__ import annotations

import pytest

from scripts.ingest_fund_holdings import Holding, parse_holdings


def test_parse_holdings_accepts_code_weight_and_name() -> None:
    holdings = parse_holdings("600519:8.50:贵州茅台,000858:7.2:五粮液")
    assert holdings == [
        Holding(symbol="600519", weight="8.50", name="贵州茅台"),
        Holding(symbol="000858", weight="7.2", name="五粮液"),
    ]


def test_parse_holdings_allows_missing_name_and_spaces() -> None:
    holdings = parse_holdings(" 600519:8.5 , 000858:7.2 ")
    assert holdings == [
        Holding(symbol="600519", weight="8.5", name=""),
        Holding(symbol="000858", weight="7.2", name=""),
    ]


def test_parse_holdings_ignores_empty_segments() -> None:
    holdings = parse_holdings("600519:8.5,,000858:7.2,")
    assert len(holdings) == 2


def test_parse_holdings_rejects_non_six_digit_code() -> None:
    with pytest.raises(ValueError, match="6 位"):
        parse_holdings("60051:8.5")


def test_parse_holdings_rejects_non_numeric_weight() -> None:
    with pytest.raises(ValueError, match="权重非数值"):
        parse_holdings("600519:abc")


def test_parse_holdings_rejects_weight_out_of_range() -> None:
    with pytest.raises(ValueError, match="0~100"):
        parse_holdings("600519:120")


def test_parse_holdings_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="至少需要一项持仓"):
        parse_holdings("  ")


def test_parse_holdings_rejects_unknown_shape() -> None:
    with pytest.raises(ValueError, match="无法解析持仓项"):
        parse_holdings("600519")

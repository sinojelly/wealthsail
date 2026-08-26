"""Report provenance and fail-closed model-output tests."""

from __future__ import annotations

import json

import pytest

from backend.reports.service import (
    _attach_deterministic_fund_items,
    _fallback_draft,
    _parse_llm_draft,
    canonical_context_hash,
)


def test_context_hash_changes_when_financial_content_changes() -> None:
    first = {
        "summary": {"totalAsset": "100.00", "qualityStatus": "FRESH"},
        "positions": [{"symbol": "600519", "quantity": "1"}],
    }
    reordered = {"positions": first["positions"], "summary": first["summary"]}
    changed = {
        "summary": {"totalAsset": "101.00", "qualityStatus": "FRESH"},
        "positions": [{"symbol": "600519", "quantity": "1"}],
    }

    assert canonical_context_hash(first) == canonical_context_hash(reordered)
    assert canonical_context_hash(first) != canonical_context_hash(changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [("item_type", "TRADE_NOW"), ("severity", "URGENT")],
)
def test_model_output_rejects_unknown_report_enums(field: str, value: str) -> None:
    item = {
        "item_type": "FACT",
        "severity": "INFO",
        "title": "已知事实",
        "body": "正文",
    }
    item[field] = value

    with pytest.raises(ValueError, match=field):
        _parse_llm_draft(json.dumps({"summary": "摘要", "items": [item]}), "PRE_MARKET")


def test_model_narrative_is_not_promoted_to_fresh_evidence() -> None:
    draft = _parse_llm_draft(
        json.dumps(
            {
                "summary": "摘要",
                "items": [
                    {
                        "item_type": "FACT",
                        "severity": "INFO",
                        "title": "模型转述",
                        "body": "未经独立证据映射",
                    }
                ],
            }
        ),
        "PRE_MARKET",
    )

    assert draft["quality"] == "UNAVAILABLE"
    assert draft["policy_check_status"] == "WARNING"
    assert draft["items"][0]["quality"] == "UNAVAILABLE"


def test_fallback_action_plan_inherits_unavailable_context_quality() -> None:
    context = {
        "summary": {
            "totalAsset": "100.00",
            "cashBalance": "100.00",
            "positionsMarketValue": "0.00",
            "dailyPnl": "0.00",
            "qualityStatus": "UNAVAILABLE",
        },
        "positions": [],
        "policy": None,
    }

    draft = _fallback_draft("PRE_MARKET", context)

    assert draft["quality"] == "UNAVAILABLE"
    assert all(item["quality"] == "UNAVAILABLE" for item in draft["items"])


def test_fallback_report_describes_incomplete_valuation_without_none_or_crashing() -> None:
    """Missing prices are uncertainty, not zero and not printable ``None`` values."""
    context = {
        "summary": {
            "totalAsset": None,
            "knownValue": "125.00",
            "cashBalance": "100.00",
            "positionsMarketValue": None,
            "dailyPnl": None,
            "qualityStatus": "MISSING",
        },
        "positions": [
            {
                "name": "未发布净值基金",
                "symbol": "000001",
                "marketValue": None,
                "weight": None,
                "pnl": None,
            }
        ],
        "policy": {"single_position_limit": "20.00"},
    }

    draft = _fallback_draft("FUND_1430", context)

    rendered = f"{draft['summary']} {draft['items']}"
    assert "None" not in rendered
    assert "不可完整估值" in rendered
    assert "已知价值 125.00 元" in rendered
    assert draft["status"] == "PARTIAL"


def test_fund_report_replaces_model_estimate_with_persisted_deterministic_fact() -> None:
    draft = {
        "summary": "模型摘要",
        "items": [
            {
                "item_type": "FUND_ESTIMATE",
                "severity": "INFO",
                "title": "模型猜测",
                "body": "不应保留",
                "quality": "UNAVAILABLE",
            }
        ],
        "quality": "UNAVAILABLE",
        "status": "PARTIAL",
    }
    context = {
        "fundEstimates": [
            {
                "instrumentId": "fund-id",
                "symbol": "110011",
                "name": "易方达中小盘",
                "value": "1.0050",
                "lowerValue": "1.0015",
                "upperValue": "1.0085",
                "quality": "ESTIMATED",
                "asOf": "2026-08-26T14:30:00+08:00",
                "snapshotId": "snapshot-id",
                "metadata": {
                    "coveragePct": "70.00",
                    "disclosedOn": "2026-06-30",
                    "confirmedNav": "1.0000",
                    "explanation": "可解释估值",
                },
            }
        ]
    }

    result = _attach_deterministic_fund_items("FUND_1430", draft, context)

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["title"] == "易方达中小盘（110011）14:30 估值"
    assert item["quality"] == "ESTIMATED"
    assert "70.00%" in item["body"]
    assert item["_evidence"]["source_snapshot_id"] == "snapshot-id"

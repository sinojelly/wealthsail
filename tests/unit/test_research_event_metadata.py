"""Economic metadata describes saved evidence without upgrading event authority."""

from datetime import datetime, timedelta, timezone
from importlib import import_module, util
import json
import os
from pathlib import Path

import pytest


NOW = datetime(2026, 11, 7, 14, tzinfo=timezone.utc)
URL = "https://www.stats.gov.cn/sj/zxfb/202610/report.html"


def module():
    assert util.find_spec("backend.macro.event_metadata"), (
        "economic metadata helper missing"
    )
    return import_module("backend.macro.event_metadata")


@pytest.mark.parametrize(
    "title,expected",
    [
        ("稳市政策与互换便利执行形成业务观察窗口", "MONETARY_POLICY"),
        ("AI硬件反弹与资本回报约束并存", "TECHNOLOGY"),
        ("机器人控制研究进展需要接受真实交付检验", "TECHNOLOGY"),
        ("美伊军事升级与海峡重开条件继续构成高重要性风险", "GEOPOLITICS_POLICY"),
        ("俄乌核设施与能源设施风险尚未解除", "GEOPOLITICS_POLICY"),
        ("中非公共卫生论坛提供合作议题，海外医疗成本压力仍在", "INDUSTRY_EMPLOYMENT"),
        ("通信建设与火箭任务分别提供工程进展", "TECHNOLOGY"),
        ("稀土需求约束、氧化铝拟议工程与化工观点分别研究", "INDUSTRY_EMPLOYMENT"),
        ("输入日历安排公布10月CPI及PPI", "DOMESTIC_GROWTH"),
        ("俄美乌阿布扎比会谈", "GEOPOLITICS_POLICY"),
        ("创业板指数单只样本权重上限调整拟生效", "FINANCIAL_MARKET"),
        ("制造业采购经理指数运行情况", "DOMESTIC_GROWTH"),
        ("央行银企会谈讨论融资需求", "MONETARY_POLICY"),
        ("未提供可辨认主题的记录", "UNCLASSIFIED"),
    ],
)
def test_closed_category_inference_covers_original_event_topics(title, expected):
    assert module().event_metadata({"title": title}, [], NOW)["category"] == expected


def test_latest_saved_source_publication_is_labelled_without_becoming_occurrence():
    value = module().event_metadata(
        {"title": "普通记录"},
        [
            {"publishedAt": "2026-11-06T23:00:00Z"},
            {"publishedDate": "2026-11-07"},
            {"publishedAt": (NOW + timedelta(hours=1)).isoformat()},
        ],
        NOW,
    )
    assert value["sourcePublishedAt"] == "2026-11-06T23:00:00Z"
    assert value["sourcePublicationBasis"] == "SOURCE_PUBLICATION"
    assert "occurredAt" not in value and "factVerification" not in value


def evidence(**updates):
    return {
        "url": URL,
        "title": "制造业采购经理指数运行情况",
        "quote": "制造业采购经理指数为49.3%，比上月下降0.8个百分点。",
        "contentHash": "a" * 64,
        "retrievedAt": NOW.isoformat(),
        "verification": "SOURCE_CHECKED",
        **updates,
    }


def test_frozen_document_quote_and_title_match_are_display_only_proof():
    source = {
        "url": URL,
        "title": "制造业采购经理指数运行情况",
        "quote": "制造业采购经理指数为49.3%",
    }
    value = module().source_metadata(source, {"documents": [evidence()]}, NOW)
    assert value == {"frozenTitleMatched": True, "frozenQuoteMatched": True}
    assert "verification" not in value and "quoteMatched" not in value


def test_candidate_index_title_alone_does_not_verify_an_unseen_body_quote():
    source = {
        "url": URL,
        "title": "制造业采购经理指数运行情况",
        "quote": "虚构的订单改善原文。",
    }
    row = evidence()
    row.pop("quote")
    row.pop("verification")
    value = module().source_metadata(source, {"candidateIndex": [row]}, NOW)
    assert value == {"frozenTitleMatched": True, "frozenQuoteMatched": False}


@pytest.mark.parametrize("changed", ["4.93%", "-49.3%", "+49.3%", "49.3", "49.35%"])
def test_title_matching_never_erases_a_conflicting_numeric_value_or_unit(changed):
    row = evidence(title="制造业采购经理指数当期下降49.3%")
    source = {
        "url": URL,
        "title": row["title"].replace("49.3%", changed),
        "quote": row["quote"],
    }
    assert module().source_metadata(source, {"documents": [row]}, NOW) == {
        "frozenTitleMatched": False,
        "frozenQuoteMatched": False,
    }


def test_title_matching_preserves_saved_date_separators():
    row = evidence(title="2026-11-07制造业采购经理指数运行情况")
    source = {
        "url": URL,
        "title": row["title"].replace("2026-11-07", "20261107"),
        "quote": row["quote"],
    }
    assert (
        module().source_metadata(source, {"documents": [row]}, NOW)[
            "frozenTitleMatched"
        ]
        is False
    )


@pytest.mark.parametrize(
    "title",
    [
        "制造业采购经理指数当期下降49.3%",
        "财联社：制造业采购经理指数当期下降49.3%",
        "制造业采购经理指数当期下降49.3% | 财联社",
    ],
)
def test_title_numeric_guard_retains_matching_headline_and_publisher_wrappers(title):
    row = evidence(title="国家统计局：制造业采购经理指数当期下降49.3%")
    source = {"url": URL, "title": title, "quote": row["quote"]}
    assert module().source_metadata(source, {"documents": [row]}, NOW) == {
        "frozenTitleMatched": True,
        "frozenQuoteMatched": True,
    }


@pytest.mark.parametrize("changed", ["4.93%", "-49.3%", "+49.3%", "49.3"])
def test_quote_matching_preserves_numeric_decimal_sign_and_percent_evidence(changed):
    row = evidence()
    source = {
        "url": URL,
        "title": row["title"],
        "quote": row["quote"].replace("49.3%", changed),
    }
    value = module().source_metadata(source, {"documents": [row]}, NOW)
    assert value == {"frozenTitleMatched": True, "frozenQuoteMatched": False}


def test_quote_cannot_match_only_a_prefix_of_a_larger_number():
    row = evidence(quote="制造业采购经理指数为49.35%，比上月下降0.8个百分点。")
    source = {"url": URL, "title": row["title"], "quote": "制造业采购经理指数为49.3"}
    assert (
        module().source_metadata(source, {"documents": [row]}, NOW)[
            "frozenQuoteMatched"
        ]
        is False
    )


def test_quote_matching_preserves_saved_date_punctuation():
    row = evidence(quote="数据发布于2026-11-07，当期制造业指数为49.3%。")
    source = {
        "url": URL,
        "title": row["title"],
        "quote": row["quote"].replace("2026-11-07", "20261107"),
    }
    assert (
        module().source_metadata(source, {"documents": [row]}, NOW)[
            "frozenQuoteMatched"
        ]
        is False
    )


def test_quote_cannot_drop_a_unicode_minus_before_a_number():
    row = evidence(quote="制造业采购经理指数为−49.3%，比上月下降0.8个百分点。")
    source = {
        "url": URL,
        "title": row["title"],
        "quote": "49.3%，比上月下降0.8个百分点。",
    }
    assert (
        module().source_metadata(source, {"documents": [row]}, NOW)[
            "frozenQuoteMatched"
        ]
        is False
    )


@pytest.mark.parametrize(
    "damage", ["wrong_url", "bad_hash", "future", "wrong_title", "wrong_quote"]
)
def test_readable_metadata_never_checks_unrelated_missing_or_future_evidence(damage):
    row = evidence()
    source = {"url": URL, "title": row["title"], "quote": row["quote"]}
    if damage == "wrong_url":
        row["url"] = "https://www.stats.gov.cn/different.html"
    elif damage == "bad_hash":
        row["contentHash"] = "NOT-A-HASH"
    elif damage == "future":
        row["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    elif damage == "wrong_title":
        row["title"] = "完全不同的税务公告"
    else:
        row["quote"] = "这篇原文没有被引用的任何句子。"
    value = module().source_metadata(source, {"documents": [row]}, NOW)
    assert value["frozenQuoteMatched"] is False
    if damage != "wrong_quote":
        assert value["frozenTitleMatched"] is False


@pytest.mark.skipif(
    not os.environ.get("WEALTHSAIL_PREVIEW_FULL_AUDIT"),
    reason="Complete private audit export not supplied",
)
def test_complete_original_events_use_saved_metadata_without_fabricating_checks():
    from backend.macro.research_batch import evaluate_research_batch
    from backend.macro.research_frozen_bundle import decode_frozen_bundle

    bundle = json.loads(
        Path(os.environ["WEALTHSAIL_PREVIEW_FULL_AUDIT"]).read_text(encoding="utf-8")
    )
    at = datetime.fromisoformat(bundle["validatedAt"])
    frozen = decode_frozen_bundle(
        bundle, "d7810690-ef13-44ad-8591-d3699f3f589a", observed_at=at
    )
    assert (
        frozen.material_hashes["PACKET"]
        == "47144a95b8e180c3df86e7712be81c1984e5672c6caffb40b2952a4ced5d3ef5"
    )
    report = evaluate_research_batch(
        frozen.raw,
        frozen.packet,
        frozen.source_checks,
        research_id=frozen.research_id,
        validated_at=at,
    )["formalReport"]
    assert len(report["events"]) == 8 and len(report["upcoming"]) == 3
    assert len(report["sources"]) == 24
    observations = report["events"] + report["upcoming"]
    assert all(row["category"] != "UNCLASSIFIED" for row in observations)
    assert all(
        row["sourcePublicationBasis"] == "SOURCE_PUBLICATION" for row in observations
    )
    assert all(
        row.get("factVerification", "UNVERIFIED") == "UNVERIFIED"
        for row in observations
    )
    assert all("occurredAt" not in row for row in observations)
    assert sum(source["frozenTitleMatched"] for source in report["sources"]) == 1
    assert sum(source["frozenQuoteMatched"] for source in report["sources"]) == 1
    assert (
        sum(source["verification"] == "SOURCE_CHECKED" for source in report["sources"])
        == 1
    )
    sources = {source["id"]: source for source in report["sources"]}
    assert all(
        not any(sources[ref]["frozenTitleMatched"] for ref in event["sourceIds"])
        for event in report["events"]
    )
    assert {row["sourcePublishedAt"] for row in observations} == {
        "2026-11-07",
        "2026-11-06",
        "2026-10-31",
    }

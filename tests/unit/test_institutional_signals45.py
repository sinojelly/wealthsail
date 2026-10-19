"""Public-media item boundaries and evidence provenance for institutional clues."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.integrations import alpha_collectors

NOW = datetime(2026, 10, 19, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


async def collect_cls(rows, monkeypatch):
    monkeypatch.setattr(alpha_collectors, "now_shanghai", lambda: NOW)
    pages = [rows, []]

    async def fetch(uri, *, params):
        assert uri == alpha_collectors.CLS_URL
        return {"errno": 0, "data": {"roll_data": pages.pop(0)}}

    return await alpha_collectors._cls(fetch, NOW)


def article(identity, title, content, *, level="B"):
    return {
        "id": identity,
        "title": title,
        "content": content,
        "ctime": int((NOW - timedelta(minutes=10)).timestamp()),
        "level": level,
    }


@pytest.mark.asyncio
async def test_cls_splits_thirteen_numbered_news_before_the_old_1000_character_cut(
    monkeypatch,
):
    facts = [
        "人工智能产业公司发布算力订单。" + "披露经营事实。" * 120,
        "摩根士丹利研报预计AI算力需求增长48%。",
        "全球人形机器人销量超过去年全年。",
        "长鑫科技预盈1310亿元并公告回购20亿元。",
        "德明利预告扭亏且净利润86亿元。",
        "山东黄金拟注销式回购公司股份。",
        "稀土产业龙头披露整合方案。",
        "固态电池量产验证取得进展。",
        "卫星互联网公司发布组网计划。",
        "中药公司披露临床研究结果。",
        "券商评级上调银行盈利预测。",
        "电力龙头披露新增订单。",
        "恒生科技ETF获大额净申购，资金持续流入。",
    ]
    content = (
        "<p>" + " ".join(f"{i}、{fact}" for i, fact in enumerate(facts, 1)) + "</p>"
    )
    parent = article(45001, "财联社10月19日早间新闻精选", content)
    result = await collect_cls([parent], monkeypatch)
    rows = result["catalysts"]
    assert len(rows) == 13
    by_number = {row["itemOrdinal"]: row for row in rows}
    assert by_number[13]["content"] == facts[12]
    assert by_number[6]["title"] == facts[5].rstrip("。")
    assert len({row["id"] for row in rows}) == 13
    assert {row["sourceUri"] for row in rows} == {"https://www.cls.cn/detail/45001"}
    assert {row["parentSourceId"] for row in rows} == {"cls:45001"}
    assert {row["parentTitle"] for row in rows} == {parent["title"]}
    assert {row["publishedAt"] for row in rows} == {"2026-10-19T08:20:00+08:00"}
    assert {row["factVerification"] for row in rows} == {"UNVERIFIED"}
    again = await collect_cls([parent], monkeypatch)
    assert [row["id"] for row in again["catalysts"]] == [row["id"] for row in rows]


@pytest.mark.asyncio
async def test_cls_circled_items_are_filtered_separately_without_parent_sector_leak(
    monkeypatch,
):
    result = await collect_cls(
        [
            article(
                45002,
                "晚间新闻精选",
                "①摩根士丹利研报上调AI算力需求预测。②奥地利航空发现可疑物体改航线。"
                "③海外发生零星疫情。④卫星制造公司接获火箭发动机大额订单。",
            ),
            article(
                45003, "《新闻联播》要闻25条", "人工智能政策与黄金金融投资。", level="A"
            ),
            article(
                45004, "海外居民楼火灾", "航空人员参与救援，银行提供帮助。", level="A"
            ),
            article(
                45005,
                "疫苗公司临床结果积极并上调盈利预期",
                "应对海外疫情的新药取得经营进展。",
            ),
            article(45006, "炼油厂火灾导致停产原油供应收缩", "公司披露生产经营影响。"),
        ],
        monkeypatch,
    )
    titles = [row["title"] for row in result["catalysts"]]
    assert len(titles) == 4
    assert not any(
        "改航线" in title or "新闻联播" in title or "居民楼" in title
        for title in titles
    )
    first = next(row for row in result["catalysts"] if row.get("itemOrdinal") == 1)
    assert first["sectorBuckets"] == ["AI_COMPUTE_SEMICONDUCTOR"]
    assert result["coverage"]["filteredNoiseCount"] == 4


@pytest.mark.asyncio
async def test_cls_does_not_split_unnumbered_digest_or_ordinary_decimal_numbers(
    monkeypatch,
):
    result = await collect_cls(
        [
            article(45007, "投资日历", "AI会议。机器人比赛。黄金论坛。"),
            article(
                45008,
                "AI公司预计净利润增长1.5倍",
                "一季度利润为2.3亿元，净值为1.2345。",
            ),
        ],
        monkeypatch,
    )
    assert {row["id"] for row in result["catalysts"]} == {"cls:45007", "cls:45008"}
    assert all("itemOrdinal" not in row for row in result["catalysts"])


@pytest.mark.asyncio
async def test_broadcast_summary_is_rejected_even_if_it_has_a_digest_title_and_sectors(
    monkeypatch,
):
    result = await collect_cls(
        [
            article(
                45009,
                "《新闻联播》要闻与早间新闻精选",
                "1、AI算力新增政策。2、黄金金融产业发展。",
            )
        ],
        monkeypatch,
    )
    assert result["catalysts"] == []
    assert result["coverage"]["filteredNoiseCount"] == 1


@pytest.mark.asyncio
async def test_fire_equipment_company_orders_are_not_filtered_as_a_social_accident(
    monkeypatch,
):
    result = await collect_cls(
        [
            article(
                45010,
                "消防公司获得海外零星火灾后设备订单",
                "公司披露消防设备采购合同与新增生产经营收入。",
            )
        ],
        monkeypatch,
    )
    assert [row["id"] for row in result["catalysts"]] == ["cls:45010"]


@pytest.mark.asyncio
async def test_foreign_city_fire_is_filtered_but_refinery_supply_and_launch_impacts_remain(
    monkeypatch,
):
    result = await collect_cls(
        [
            article(
                45011, "英国伦敦一住宅起火4人受伤", "消防人员赶往现场。", level="A"
            ),
            article(
                45012, "美国加州山火继续蔓延", "当地救援部门继续疏散居民。", level="A"
            ),
            article(45013, "俄罗斯炼厂火灾影响原油供应", "炼厂维修期间暂停生产。"),
            article(
                45014, "山火导致商业航天火箭发射计划推迟", "发射服务公司调整履约日程。"
            ),
        ],
        monkeypatch,
    )
    assert {row["id"] for row in result["catalysts"]} == {"cls:45013", "cls:45014"}


@pytest.mark.asyncio
async def test_digest_expansion_is_bounded_and_reports_incomplete_retention(
    monkeypatch,
):
    parents = [
        article(
            45100 + parent,
            "早间新闻精选",
            " ".join(
                f"{i}、AI算力公司{parent}-{i}发布业绩预告。" for i in range(1, 41)
            ),
        )
        for parent in range(9)
    ]
    result = await collect_cls(parents, monkeypatch)
    assert len(result["catalysts"]) == 300
    assert result["coverage"]["truncated"] is True
    assert result["coverage"]["retainedCount"] == 300
    assert result["status"] == "PARTIAL"


def test_institutional_clues_preserve_media_provenance_and_distinguish_monetary_operations():
    from backend.integrations.institutional_signals import (
        extract_media_institutional_signals,
    )

    common = {
        "sourceUri": "https://www.cls.cn/detail/45001",
        "publishedAt": "2026-10-19T08:20:00+08:00",
        "retrievedAt": "2026-10-19T08:25:00+08:00",
        "availableAt": "2026-10-19T08:25:00+08:00",
        "factVerification": "UNVERIFIED",
        "parentSourceId": "cls:45001",
        "importance": "HIGH",
    }
    facts = [
        {
            "id": "cls:45001:item:1",
            "title": "摩根士丹利研报预计AI算力需求增长48%",
            "content": "大行上调需求预测。",
        },
        {
            "id": "cls:45001:item:2",
            "title": "山东黄金拟注销式回购20亿元",
            "content": "公司公告回购计划，尚未执行。",
        },
        {
            "id": "cls:45001:item:3",
            "title": "德明利预计扭亏净利润86亿元",
            "content": "公司披露业绩预告。",
        },
        {
            "id": "cls:45001:item:4",
            "title": "黄金ETF获大额净申购",
            "content": "媒体报道昨日净申购3亿元。",
        },
        {
            "id": "cls:45001:item:5",
            "title": "央行开展1100亿元逆回购",
            "content": "公开市场政策操作。",
        },
    ]
    rows = extract_media_institutional_signals(
        [{**common, **fact} for fact in facts], NOW
    )
    assert {row["kind"] for row in rows} == {
        "RESEARCH_VIEW",
        "CORPORATE_BUYBACK",
        "EARNINGS_UPGRADE",
        "ETF_MEDIA_FLOW",
    }
    assert {row["mediaEvidenceId"] for row in rows} == {
        f"cls:45001:item:{i}" for i in range(1, 5)
    }
    assert all(row["sourceIds"] == [row["mediaEvidenceId"]] for row in rows)
    assert all(row["parentSourceId"] == "cls:45001" for row in rows)
    assert all(row["factVerification"] == "UNVERIFIED" for row in rows)
    assert all(row["institutionAttribution"] == "UNKNOWN" for row in rows)
    buyback = next(row for row in rows if row["kind"] == "CORPORATE_BUYBACK")
    assert buyback["cancellationBuyback"] is True
    assert "尚未执行" in buyback["summary"]
    assert not any(
        "value" in row or "institutionNetSubscription" in row for row in rows
    )


def test_media_institutional_clues_drop_future_and_unsafe_source_without_mutating_input():
    from copy import deepcopy
    from backend.integrations.institutional_signals import (
        extract_media_institutional_signals,
    )

    rows = [
        {
            "id": "cls:1",
            "title": "中金研报上调芯片需求预测",
            "content": "经营展望",
            "sourceUri": "https://www.cls.cn/detail/1",
            "publishedAt": "2026-10-19T09:00:00+08:00",
            "retrievedAt": "2026-10-19T08:00:00+08:00",
        },
        {
            "id": "cls:2",
            "title": "公司注销回购股份",
            "content": "方案",
            "sourceUri": "http://127.0.0.1/private",
            "publishedAt": "2026-10-19T08:00:00+08:00",
            "retrievedAt": "2026-10-19T08:00:00+08:00",
        },
    ]
    before = deepcopy(rows)
    assert extract_media_institutional_signals(rows, NOW) == []
    assert rows == before

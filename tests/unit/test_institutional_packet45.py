from datetime import timedelta

from test_daily_research import NOW


def test_institutional_packet_keeps_media_parent_and_separately_defined_real_etf_facts():
    from backend.macro.institutional_packet import institutional_packet

    media = [
        {
            "id": "cls-child-13",
            "parentSourceId": "cls-parent",
            "title": "大摩研报上调AI需求预测",
            "content": "预测仍为机构观点。",
            "sourceUri": "https://www.cls.cn/detail/123",
            "publishedAt": NOW.isoformat(),
            "retrievedAt": NOW.isoformat(),
            "factVerification": "UNVERIFIED",
        }
    ]
    flow = {
        "id": "sse-flow",
        "metric": "ETF_SHARE_CHANGE",
        "value": "50000",
        "unit": "份",
        "fundCode": "512000",
        "fundName": "券商ETF",
        "referenceDate": "2026-10-15",
        "comparisonDate": "2026-10-14",
        "sourceUri": "https://query.sse.com.cn/commonQuery.do",
        "comparisonSourceUri": "https://query.sse.com.cn/commonQuery.do?day=older",
        "retrievedAt": NOW.isoformat(),
        "institutionAttribution": "institution claims are invalid",
        "definition": "两日清算份额差，不代表机构现金净申购",
        "private": "never publish",
    }
    result = institutional_packet(
        media,
        {"signals": [flow], "sourceResults": {"SSE_ETF_SHARES": {"status": "SUCCESS"}}},
        NOW,
    )
    assert result["researchViews"][0]["sourceIds"] == ["cls-child-13"]
    assert result["researchViews"][0]["parentSourceId"] == "cls-parent"
    assert result["researchViews"][0]["factVerification"] == "UNVERIFIED"
    assert result["etfFlows"][0]["value"] == "50000"
    assert result["etfFlows"][0]["institutionAttribution"] == "UNKNOWN"
    assert result["etfFlows"][0]["definition"] == flow["definition"]
    assert "never publish" not in str(result)
    flow["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    assert institutional_packet([], {"signals": [flow]}, NOW)["etfFlows"] == []

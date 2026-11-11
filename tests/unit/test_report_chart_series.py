"""Read-only saved-report curves preserve JSON while avoiding repeated copies."""

from copy import copy, deepcopy
import json
from unittest.mock import AsyncMock

from fastapi.encoders import jsonable_encoder
import pytest

from backend.common.report_chart_series import freeze_chart_series, freeze_report_chart_series


def curve(**changes):
    return {
        "fundCode": "008298", "asOf": "2026-10-27", "unit": "CNY/份",
        "priceBasis": "UNIT_NAV", "retrievedAt": "2026-10-28T12:00:00+08:00",
        "sourceUri": "https://fund.eastmoney.com/008298.html",
        "points": [{"date": "2026-10-26", "value": "1.5001"},
                   {"date": "2026-10-27", "value": "1.5000"}],
        "metadata": {"nullable": None, "flags": [True, 2, 2.5, "中文"]},
        **changes,
    }


def report():
    return {
        "summary": "全部观点保留", "valueDirections": [{"key": "hardware", "plans": [{
            "currentPrice": "1.5000", "tracks": [{"track": "LEFT_VALUE", "entryZoneMin": "1.40"}],
            "series": curve(), "actionableZones": {"series": curve()},
        }]}],
        "researchTopics": [{"key": "memory", "plans": [{"series": curve()}]}],
        "mentionedFunds": [{"series": curve(), "actionableZones": {"series": curve()}}],
        "quantitativeScan": {"items": [{"series": curve()}]},
    }


@pytest.mark.parametrize("mutate", [
    lambda x: x.__setitem__("asOf", "other"),
    lambda x: x.__delitem__("asOf"),
    lambda x: x.clear(), lambda x: x.pop("asOf"), lambda x: x.popitem(),
    lambda x: x.setdefault("extra", 1), lambda x: x.update(extra=1),
    lambda x: x.__ior__({"extra": 1}), lambda x: x.__init__({"extra": 1}),
    lambda x: x["metadata"].__setitem__("extra", 1),
    lambda x: x["points"].__setitem__(0, {}),
    lambda x: x["points"].__setitem__(slice(None), []),
    lambda x: x["points"].__delitem__(0),
    lambda x: x["points"].append({}), lambda x: x["points"].extend([{}]),
    lambda x: x["points"].insert(0, {}), lambda x: x["points"].pop(),
    lambda x: x["points"].remove(x["points"][0]), lambda x: x["points"].clear(),
    lambda x: x["points"].reverse(), lambda x: x["points"].sort(key=str),
    lambda x: x["points"].__iadd__([{}]), lambda x: x["points"].__imul__(2),
    lambda x: x["points"].__init__([{}]),
    lambda x: x["points"][0].__setitem__("value", "999"),
    lambda x: x["metadata"]["flags"].append("changed"),
])
def test_every_nested_chart_container_rejects_mutation(mutate):
    original = curve()
    frozen = freeze_chart_series(original)
    with pytest.raises(TypeError, match="read-only"):
        mutate(frozen)
    assert frozen == original


def test_report_copies_share_only_immutable_complete_equal_curves():
    original = report()
    saved = deepcopy(original)
    result = freeze_report_chart_series(original)
    plan = result["valueDirections"][0]["plans"][0]
    first = plan["series"]
    assert first is plan["actionableZones"]["series"]
    assert first is result["researchTopics"][0]["plans"][0]["series"]
    assert first is result["mentionedFunds"][0]["actionableZones"]["series"]
    assert first is result["quantitativeScan"]["items"][0]["series"]
    assert copy(first) is deepcopy(first) is first
    assert deepcopy(first["points"]) is first["points"]
    assert result == original == saved
    assert json.loads(json.dumps(result, ensure_ascii=False)) == saved
    assert json.loads(json.dumps(jsonable_encoder(result), ensure_ascii=False)) == saved
    assert freeze_report_chart_series(result) is result

    copied = deepcopy(result)
    copied_plan = copied["valueDirections"][0]["plans"][0]
    assert copied_plan["series"] is first
    copied_plan["tracks"][0]["entryZoneMin"] = "1.30"
    assert plan["tracks"][0]["entryZoneMin"] == "1.40"
    assert original == saved

    # Freezing a detached read copy never freezes the persistence/cache input.
    assert type(original["valueDirections"][0]["plans"][0]["series"]) is dict
    original["valueDirections"][0]["plans"][0]["series"]["points"][0]["value"] = "3"
    assert first["points"][0]["value"] == "1.5001"


@pytest.mark.parametrize("changes", [
    {"asOf": "2026-10-26"}, {"retrievedAt": "2026-10-28T13:00:00+08:00"},
    {"sourceUri": "https://fund.eastmoney.com/other"},
    {"metadata": {"nullable": None, "flags": [False, 2, 2.5, "中文"]}},
    {"points": list(reversed(curve()["points"]))},
])
def test_same_fund_with_different_complete_curve_is_never_merged(changes):
    first, second = curve(), curve(**changes)
    frozen = freeze_report_chart_series({"plans": [{"series": first}, {"series": second}]})
    a, b = [row["series"] for row in frozen["plans"]]
    assert a is not b and a == first and b == second


def test_deepcopy_visits_business_fields_without_recopying_chart_points():
    visits = []

    class Point(dict):
        def __deepcopy__(self, memo):
            visits.append(id(self))
            return deepcopy(dict(self), memo)

    source_curve = curve(points=[Point(date=str(i), value=str(i)) for i in range(500)])
    original = {"plans": [{"series": deepcopy(source_curve), "tracks": [{"state": "READY"}]} for _ in range(12)]}
    # Recreate distinct JSON-decoded point containers, as persisted reports do.
    for plan in original["plans"]:
        plan["series"]["points"] = [Point(row) for row in plan["series"]["points"]]
    visits.clear()
    deepcopy(original)
    assert len(visits) == 6000
    visits.clear()
    frozen = freeze_report_chart_series(original)
    for _ in range(5):
        copied = deepcopy(frozen)
        assert copied["plans"][0]["tracks"] is not frozen["plans"][0]["tracks"]
        assert copied["plans"][0]["series"] is frozen["plans"][0]["series"]
    assert not visits


def test_narrative_projection_keeps_only_read_only_chart_snapshots_shared():
    from backend.macro.model_projection import readable_fund_codes

    original = {"summary": "关注008298007466", "plan": {"series": curve()}}
    plain = readable_fund_codes(original, {"008298", "007466"})
    frozen = freeze_report_chart_series(original)
    projected = readable_fund_codes(frozen, {"008298", "007466"})

    assert projected == plain
    assert projected["summary"] == "关注008298与007466"
    assert projected["plan"]["series"] is frozen["plan"]["series"]
    assert type(plain["plan"]["series"]) is dict
    plain["plan"]["series"]["points"][0]["value"] = "ordinary mutable copy"
    assert original["plan"]["series"]["points"][0]["value"] == "1.5001"
    with pytest.raises(TypeError):
        projected["plan"]["series"]["metadata"]["flags"].append("changed")


def test_read_only_curve_metadata_is_preserved_while_plain_narrative_rules_stay_unchanged():
    from backend.macro.model_projection import readable_fund_codes

    original = curve(metadata={"title": "008298007466", "summary": "原始曲线说明", "extra": [None]})
    frozen = freeze_chart_series(original)
    result = readable_fund_codes(
        {"summary": "关注008298007466", "plan": {"series": frozen}}, {"008298", "007466"},
    )
    assert result["summary"] == "关注008298与007466"
    assert result["plan"]["series"] is frozen
    assert result["plan"]["series"] == original
    assert result["plan"]["series"]["metadata"]["title"] == "008298007466"
    assert readable_fund_codes(original, {"008298", "007466"})["metadata"]["title"] == "008298与007466"


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_nav", [False, True])
async def test_only_read_only_refresh_shares_new_server_curves(repair_nav):
    from backend.investment.live_fund_plans import refresh_report_fund_plans
    from tests.unit.test_live_fund_plans_v6 import LocalHistory, NOW, history, old_plan

    original = {"valueDirections": [{"plans": [old_plan(), old_plan()]}]}
    saved = deepcopy(original)
    result = await refresh_report_fund_plans(
        LocalHistory([history()]), original, NOW, include_series=True, repair_nav=repair_nav,
    )
    a, b = result["valueDirections"][0]["plans"]
    assert len(a["series"]["points"]) == 500
    assert a["currentPrice"] == b["currentPrice"] == "1.5000"
    assert original == saved
    if repair_nav:
        a["series"]["points"][0]["value"] = "changed"
        assert b["series"]["points"][0]["value"] != "changed"
    else:
        assert a["series"] is b["series"] is a["actionableZones"]["series"]
        with pytest.raises(TypeError):
            a["series"]["points"][0]["value"] = "changed"


@pytest.mark.asyncio
async def test_daily_reader_freezes_saved_curves_before_quantitative_report_copies(monkeypatch):
    from backend.macro import daily_repository
    from backend.investment import quantitative_scan
    from tests.unit.test_daily_repository import NOW, run

    original = {"summary": "ready", "retained": {"series": curve()}}
    conn = AsyncMock()
    conn.fetchrow.return_value = run(status="READY", recorded_at=NOW, payload=original)
    project = quantitative_scan.report_with_quantitative_scan
    seen = []

    def observe(value, packet, now):
        frozen = value["retained"]["series"]
        seen.append(frozen)
        assert deepcopy(frozen) is frozen
        with pytest.raises(TypeError):
            frozen["points"].append({})
        return project(value, packet, now)

    monkeypatch.setattr(quantitative_scan, "report_with_quantitative_scan", observe)
    result = await daily_repository.read_daily_research(conn, NOW)
    assert result["report"]["retained"]["series"] is seen[0]
    assert type(original["retained"]["series"]) is dict
    original["retained"]["series"]["points"].append({"date": "later", "value": "1"})
    assert len(seen[0]["points"]) == 2

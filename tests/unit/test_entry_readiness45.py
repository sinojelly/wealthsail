"""An in-range price must not masquerade as an untriggered technical entry."""

from copy import deepcopy

import pytest

from backend.investment.study_price_watch import price_watch
from backend.jobs.alpha_opportunities import compare_frozen_price, _fund_message
from test_study_price_watch44 import NOW, plan, quote


def frozen(track="RIGHT_TREND", technical="WATCH"):
    item = plan()
    item.update(selectedTrack=track, methodVersion="actionable-zones-v3")
    item["tracks"][0].update(track=track)
    if technical is None:
        item["tracks"][0].pop("state")
    else:
        item["tracks"][0]["state"] = technical
    return item


def estimate(value="1.07"):
    return quote(value, quality="ESTIMATED", asOf=NOW.isoformat())


@pytest.mark.parametrize(
    "track,technical,state",
    [
        ("RIGHT_TREND", "WATCH", "WATCH"),
        ("LEFT_VALUE", "WATCH", "WATCH"),
        ("RIGHT_TREND", None, "WATCH"),
        ("RIGHT_TREND", "CANDIDATE", "IN_ENTRY_ZONE"),
    ],
)
def test_1430_prices_in_zone_require_frozen_technical_candidate(
    track, technical, state
):
    item = frozen(track, technical)
    original = deepcopy(item)
    result = compare_frozen_price(item, estimate(), NOW)
    assert result["state"] == state
    assert result.get("inEntryZone") is True
    assert result.get("technicalCandidate") is (technical == "CANDIDATE")
    if technical != "CANDIDATE":
        assert result["reason"] in {
            "TECHNICAL_NOT_TRIGGERED",
            "TECHNICAL_STATE_UNKNOWN",
        }
    assert item == original


@pytest.mark.parametrize(
    "value,state", [("0.94", "STOP_LOSS"), ("1.40", "TAKE_PROFIT_T1")]
)
def test_untriggered_right_track_still_checks_frozen_stop_and_profit(value, state):
    assert compare_frozen_price(frozen(), estimate(value), NOW)["state"] == state


@pytest.mark.parametrize(
    "value,state", [("0.94", "STOP_LOSS"), ("1.40", "TAKE_PROFIT")]
)
def test_tracking_untriggered_right_track_keeps_stop_and_profit_priority(value, state):
    result = price_watch(frozen(), confirmed=quote(value), now=NOW)
    assert result["state"] == state
    assert result["technicalCandidate"] is False


@pytest.mark.parametrize(
    "holding,state", [("UNKNOWN", "OBSERVING"), ("CONFIRMED", "HOLDING")]
)
def test_tracking_in_zone_waits_for_technical_trigger_without_fabricating_holding(
    holding, state
):
    item = frozen()
    before = deepcopy(item)
    result = price_watch(
        item, confirmed=quote("1.07"), holding={"state": holding}, now=NOW
    )
    assert result["state"] == state
    assert result.get("inEntryZone") is True
    assert result.get("technicalCandidate") is False
    assert "技术条件待触发" in result["reason"]
    assert item == before


def test_tracking_a_true_right_candidate_preserves_entry_and_missing_state_is_unknown():
    assert (
        price_watch(frozen(technical="CANDIDATE"), confirmed=quote("1.07"), now=NOW)[
            "state"
        ]
        == "ENTRY_ZONE"
    )
    result = price_watch(frozen(technical=None), confirmed=quote("1.07"), now=NOW)
    assert result["state"] == "UNKNOWN"


def test_1430_message_calls_in_zone_watch_a_pending_trigger_instead_of_outside_or_buy():
    item = frozen()
    checked = compare_frozen_price(item, estimate(), NOW)
    message = _fund_message(
        [
            {
                "check": checked,
                "snapshot": {"actionablePlan": item},
                "fund_code": "000051",
                "fund_name": "测试基金",
                "planOrigin": "FROZEN_STUDY",
            }
        ],
        NOW.date(),
    )
    assert "价格在区间内，技术条件待触发" in message
    assert "区间外观察" not in message and "进入冻结入场区间" not in message

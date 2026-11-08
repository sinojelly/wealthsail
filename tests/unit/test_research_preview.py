"""Historical preview reads immutable evidence and never acquires live authority."""

from copy import deepcopy
from datetime import datetime, timedelta
from importlib import import_module, util
import json
from uuid import UUID

import pytest

from backend.macro.research_acceptance_repository import freeze_artifact
from backend.macro.research_frozen_bundle import decode_frozen_bundle
from backend.integrations.nav_history_proof import nav_points_digest
from tests.unit.test_research_autodowngrade_audit import public_failure, RUN

BATCH = UUID("10000000-0000-4000-8000-000000000001")


def module():
    assert util.find_spec("backend.macro.research_preview"), (
        "historical preview service missing"
    )
    return import_module("backend.macro.research_preview")


def case():
    value, at = public_failure()
    packet, raw = value["packet"], value["raw"]
    template_anchor = deepcopy(packet["discoveryActionableZones"][0])
    template_universe = deepcopy(packet["discoveryUniverse"][0])
    template_direction = deepcopy(raw["valueDirections"][0])
    packet["discoveryActionableZones"], packet["discoveryUniverse"] = [], []
    raw["valueDirections"] = []
    for index, code in enumerate(("018344", "013477", "011035")):
        identity = str(UUID(int=100 + index))
        anchor = {
            **deepcopy(template_anchor),
            "subjectId": identity,
            "fundCode": code,
            "label": f"离线样例基金{index}",
        }
        packet["discoveryActionableZones"].append(anchor)
        packet["discoveryUniverse"].append(
            {
                **template_universe,
                "subjectId": identity,
                "fundCode": code,
                "label": anchor["label"],
            }
        )
        direction = deepcopy(template_direction)
        direction.update(
            key=f"offline-{index}",
            title=f"离线样例方向{index}",
            quantitativeFundCodes=[code],
        )
        direction["plans"][0]["subjectId"] = identity
        raw["valueDirections"].append(direction)
    packet["sourceBatchId"] = str(BATCH)
    packet["privateDeployment"] = {"token": "PRIVATE_DEPLOYMENT"}
    items = []
    for kind, body, limit in (
        ("PACKET", packet, 4 * 1024**2),
        ("RAW_MODEL", json.dumps(raw, ensure_ascii=False), 2 * 1024**2),
        ("SOURCE_CHECKS", value["sourceChecks"], 512 * 1024),
    ):
        items.append(
            {"kind": kind, **freeze_artifact(body, limit, raw=kind == "RAW_MODEL")}
        )
    bundle = {
        "researchId": RUN,
        "validatedAt": at.isoformat(),
        "complete": True,
        "items": items,
    }
    record = {
        "run_id": UUID(RUN),
        "status": "FAILED",
        "dispatched_at": datetime.fromisoformat(packet["asOf"]) + timedelta(seconds=1),
        "recorded_at": at + timedelta(seconds=1),
        "validated_at": at,
        "source_batch_id": BATCH,
        "error_class": "ResearchValidationError",
    }
    return value, bundle, record, at


def history(anchor, at):
    end = (
        datetime.fromisoformat(anchor["asOf"]).date()
        if "T" in anchor["asOf"]
        else datetime.fromisoformat(anchor["asOf"]).date()
    )
    points = [
        {"date": (end - timedelta(days=2)).isoformat(), "close": "1.01"},
        {"date": (end - timedelta(days=1)).isoformat(), "close": "1.02"},
        {"date": end.isoformat(), "close": anchor["currentPrice"]},
    ]
    code = anchor["fundCode"]
    result = {
        "fundCode": code,
        "priceBasis": "UNIT_NAV",
        "sourceUri": f"https://fund.eastmoney.com/{code}.html",
        "retrievedAt": anchor["retrievedAt"],
        "availableAt": anchor["availableAt"],
        "asOf": end.isoformat(),
        "points": points,
        "fullHistory": True,
    }
    result["historyProof"] = {
        "methodVersion": "akshare-unit-nav-full-v1",
        "complete": True,
        "fundCode": code,
        "sourceUri": result["sourceUri"],
        "queryStart": "1900-01-01",
        "queryEnd": end.isoformat(),
        "pointCount": len(points),
        "receivedRowCount": len(points),
        "firstDate": points[0]["date"],
        "lastDate": points[-1]["date"],
        "pointsDigest": nav_points_digest(points),
    }
    return result


class Conn:
    def __init__(self, bundle, record, histories=()):
        self.bundle, self.record, self.histories = bundle, record, list(histories)
        self.queries = []

    async def fetchrow(self, sql, *args):
        self.queries.append(sql)
        if "alpha_public_batches" in sql:
            return {
                "id": BATCH,
                "status": "READY",
                "completed_at": self.record["dispatched_at"] - timedelta(seconds=1),
                "payload": {"fundHistories": self.histories},
            }
        return deepcopy(self.record)

    async def fetchval(self, sql, *args):
        self.queries.append(sql)
        assert "read_validation_artifacts_admin" in sql
        value = deepcopy(self.bundle)
        if args[-1] is False:
            for row in value["items"]:
                row["content"] = None
        return value

    async def fetch(self, sql, *args):
        self.queries.append(sql)
        return [deepcopy(self.record)]

    async def execute(self, *args):
        raise AssertionError("historical preview must not write")


@pytest.mark.asyncio
async def test_full_preview_preserves_all_three_raw_funds_after_plan_downgrade_without_live_data():
    value, bundle, record, at = case()
    frozen = decode_frozen_bundle(bundle, RUN, observed_at=at + timedelta(days=1))
    histories = [
        history(anchor, at) for anchor in frozen.packet["discoveryActionableZones"]
    ]
    conn = Conn(bundle, record, histories)
    before = deepcopy((bundle, record, histories))
    result = await module().preview_research(
        conn, UUID(RUN), now=at + timedelta(days=1)
    )
    assert result["mode"] == "HISTORICAL_SIMULATION" and result["readOnly"] is True
    assert result["investmentEligible"] is False
    assert result["validatedAt"] == at.isoformat()
    assert len(result["report"]["valueDirections"]) == 3
    assert {row["fundCode"] for row in result["funds"]} == {
        "018344",
        "013477",
        "011035",
    }
    assert len(result["funds"]) == 3
    for fund in result["funds"]:
        assert fund["investmentEligible"] is False
        assert fund["anchor"]["investmentEligible"] is False
        assert fund["anchor"]["orderPriceAvailable"] is False
        assert fund["anchor"]["tracks"]
        assert fund["analysis"]["seriesKind"] == "UNIT_NAV"
        assert len(fund["analysis"]["points"]) == 3
        assert (
            float(fund["anchor"]["currentPrice"])
            == fund["analysis"]["points"][-1]["close"]
        )
        assert all(
            point["date"] <= at.date().isoformat()
            for point in fund["analysis"]["points"]
        )
    assert all(
        row["plans"] == [] and row["investmentEligible"] is False
        for row in result["report"]["valueDirections"]
    )
    assert result["opportunityContext"]["dailyResearch"]["report"] == result["report"]
    assert "PRIVATE_DEPLOYMENT" not in json.dumps(result, ensure_ascii=False)
    assert "sourceChecks" not in result and "packet" not in result
    assert (bundle, record, histories) == before
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in conn.queries)


@pytest.mark.asyncio
async def test_unfrozen_nav_history_is_missing_rather_than_a_percentile_interpolation():
    _value, bundle, record, at = case()
    result = await module().preview_research(
        Conn(bundle, record), UUID(RUN), now=at + timedelta(days=1)
    )
    assert result["state"] == "PARTIAL"
    assert len(result["funds"]) == 3
    assert all(
        row["analysis"] is None
        and "FROZEN_NAV_HISTORY_MISSING" in row["missingReasons"]
        for row in result["funds"]
    )
    assert all(row["anchor"]["tracks"] for row in result["funds"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["hash", "size", "missing", "identity", "clock", "run_clock"]
)
async def test_preview_refuses_invalid_frozen_identity_materials_or_original_clock(
    damage,
):
    _value, bundle, record, at = case()
    if damage == "hash":
        bundle["items"][0]["sha256"] = "a" * 64
    elif damage == "size":
        bundle["items"][0]["byteLength"] += 1
    elif damage == "missing":
        bundle["items"].pop()
    elif damage == "identity":
        bundle["researchId"] = str(UUID(int=900))
    elif damage == "clock":
        bundle["validatedAt"] = (at + timedelta(days=10)).isoformat()
    else:
        record["validated_at"] = at - timedelta(seconds=1)
    with pytest.raises(Exception) as caught:
        await module().preview_research(
            Conn(bundle, record), UUID(RUN), now=at + timedelta(days=1)
        )
    assert caught.value.__class__.__name__ == "ValidationError_"
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["digest", "future", "anchor", "partial"])
async def test_unreliable_history_cannot_be_displayed_as_original_frozen_nav(damage):
    _value, bundle, record, at = case()
    frozen = decode_frozen_bundle(bundle, RUN, observed_at=at + timedelta(days=1))
    row = history(frozen.packet["discoveryActionableZones"][0], at)
    if damage == "digest":
        row["historyProof"]["pointsDigest"] = "a" * 64
    elif damage == "future":
        row["availableAt"] = (at + timedelta(hours=1)).isoformat()
    elif damage == "anchor":
        row["points"][-1]["close"] = "999.99"
    else:
        row["historyProof"]["complete"] = False
    result = await module().preview_research(
        Conn(bundle, record, [row]), UUID(RUN), now=at + timedelta(days=1)
    )
    fund = next(item for item in result["funds"] if item["fundCode"] == row["fundCode"])
    assert fund["analysis"] is None
    assert "FROZEN_NAV_HISTORY_UNVERIFIED" in fund["missingReasons"]


@pytest.mark.asyncio
async def test_list_preview_runs_checks_metadata_without_replaying_or_loading_bodies(
    monkeypatch,
):
    _value, bundle, record, at = case()
    conn = Conn(bundle, record)
    result = await module().list_preview_runs(
        conn, now=at + timedelta(days=1), limit=30
    )
    assert result[0]["runId"] == RUN and result[0]["status"] == "FAILED"
    assert result[0]["canPreview"] is True
    assert result[0]["previewMissingReasons"] == []
    assert all("alpha_public_batches" not in sql for sql in conn.queries)
    bundle["items"][0]["omissionReason"] = "SIZE_LIMIT"
    result = await module().list_preview_runs(
        conn, now=at + timedelta(days=1), limit=30
    )
    assert result[0]["canPreview"] is False


def test_retained_provider_series_is_complete_when_received_count_exceeds_retained_points():
    _value, bundle, _record, at = case()
    frozen = decode_frozen_bundle(bundle, RUN, observed_at=at + timedelta(days=1))
    anchor = frozen.packet["discoveryActionableZones"][0]
    row = history(anchor, at)
    row.pop("historyProof")
    row["fullHistory"] = False
    row["providerHistoryProof"] = {
        "methodVersion": "eastmoney-nav-history-v1",
        "complete": True,
        "fundCode": row["fundCode"],
        "sourceUri": "https://api.fund.eastmoney.com/f10/lsjz",
        "retrievedAt": row["retrievedAt"],
        "queryStart": row["points"][0]["date"],
        "queryEnd": at.date().isoformat(),
        "pointCount": 3,
        "uniquePointCount": 5,
        "receivedRowCount": 5,
        "totalCount": 5,
        "pageCount": 1,
        "requiredPages": 1,
        "firstDate": row["points"][0]["date"],
        "lastDate": row["points"][-1]["date"],
        "pointsDigest": nav_points_digest(row["points"]),
    }
    analysis = module()._analysis(row, anchor, at)
    assert analysis is not None and len(analysis["points"]) == 3
    row["providerHistoryProof"]["retrievedAt"] = (at + timedelta(days=1)).isoformat()
    assert module()._analysis(row, anchor, at) is None


def test_public_context_retains_frozen_series_alpha_and_calendar_but_not_private_metadata():
    _value, bundle, _record, at = case()
    packet = decode_frozen_bundle(
        bundle, RUN, observed_at=at + timedelta(days=1)
    ).packet
    row = {
        "code": "CN_T10",
        "label": "原始公开指标",
        "frequency": "DAILY",
        "unit": "%",
        "value": 2.01,
        "asOf": "2026-11-06",
        "sourceUri": "https://example.com/facts",
        "state": "READY",
        "changes": {"sampleCount": 2, "previousValue": 2.00},
        "history": [
            {"date": "2026-11-05", "value": 2.00, "state": "READY"},
            {"date": "2026-11-06", "value": 2.01, "state": "READY"},
        ],
        "metadata": {"definitionKey": "CN_T10", "privateToken": "PRIVATE_CONTEXT"},
    }
    packet["series"] = [row]
    packet["alphaPublic"] = {
        "asOf": packet["asOf"],
        "observations": [
            {"code": "TEST_PUBLIC", "value": 1.2, "referenceDate": "2026-11-06"}
        ],
        "catalysts": [
            {
                "id": "frozen-news",
                "title": "原始公开消息",
                "sourceUri": "https://example.com/news",
            }
        ],
        "privateToken": "PRIVATE_CONTEXT",
    }
    packet["schedules"] = [
        {
            "code": "PMI",
            "status": "SCHEDULED",
            "scheduledAt": "2026-12-01T09:30:00+08:00",
        }
    ]
    result = module()._context(packet, {"summary": "checked"}, RUN, at)
    assert result["series"][0]["history"] == row["history"]
    assert result["alphaPublic"]["observations"][0]["code"] == "TEST_PUBLIC"
    assert result["alphaPublic"]["catalysts"][0]["id"] == "frozen-news"
    assert result["schedules"][0]["code"] == "PMI"
    assert result["dailyResearch"]["reportCompletedAt"] == at.isoformat()
    assert "PRIVATE_CONTEXT" not in json.dumps(result)


def test_malformed_raw_fund_identity_is_a_missing_reference_without_a_type_error():
    _value, bundle, _record, at = case()
    packet = decode_frozen_bundle(
        bundle, RUN, observed_at=at + timedelta(days=1)
    ).packet
    funds, _anchors, _universe = module()._inventory(
        {
            "valueDirections": [
                {
                    "plans": [
                        {"subjectKind": [], "subjectId": "bad", "fundCode": "018344"},
                    ]
                }
            ]
        },
        packet,
    )
    assert len(funds) <= 1


@pytest.mark.asyncio
async def test_list_omits_a_run_when_the_existing_artifact_policy_hides_it():
    from backend.errors import NotFoundError

    _value, bundle, record, at = case()

    class Hidden(Conn):
        async def fetchval(self, *_args):
            raise NotFoundError("not visible")

    result = await module().list_preview_runs(
        Hidden(bundle, record), now=at + timedelta(days=1)
    )
    assert result == []

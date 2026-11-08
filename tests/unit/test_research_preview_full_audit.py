"""Opt-in replay of complete private d781 exports; no database or AI access.

Set WEALTHSAIL_PREVIEW_FULL_AUDIT and WEALTHSAIL_PREVIEW_FULL_HISTORIES to
integrity-preserving local exports. Private frozen contents stay outside Git.
"""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
from uuid import UUID

import pytest

from backend.macro.research_frozen_bundle import decode_frozen_bundle
from backend.macro.research_preview import preview_research

RUN = "d7810690-ef13-44ad-8591-d3699f3f589a"
HASHES = {
    "PACKET": "47144a95b8e180c3df86e7712be81c1984e5672c6caffb40b2952a4ced5d3ef5",
    "RAW_MODEL": "f7379502817a35376780e5f48d13477349669fe6e1544ffd90d343cec781509b",
    "SOURCE_CHECKS": "9e1a9739d291d3f9716e5054b68d15e94dda0c420498f9ef7af42bec74f9c441",
}
EXPORT = os.environ.get("WEALTHSAIL_PREVIEW_FULL_AUDIT")
HISTORIES = os.environ.get("WEALTHSAIL_PREVIEW_FULL_HISTORIES")


@pytest.mark.skipif(
    not (EXPORT and HISTORIES), reason="Complete private audit exports not supplied"
)
@pytest.mark.asyncio
async def test_complete_original_d781_preserves_every_researched_fund_price_and_curve():
    bundle = json.loads(Path(EXPORT).read_text(encoding="utf-8"))
    historical = json.loads(Path(HISTORIES).read_text(encoding="utf-8"))
    at = datetime.fromisoformat(bundle["validatedAt"])
    frozen = decode_frozen_bundle(bundle, RUN, observed_at=at + timedelta(days=1))
    assert (
        frozen.material_hashes == HASHES
    )  # A reduced fixture cannot satisfy this test.
    assert len(frozen.packet["discoveryUniverse"]) == len(historical["histories"]) == 46
    assert historical["researchId"] == RUN
    assert historical["sourceBatchId"] == frozen.packet["sourceBatchId"]
    assert datetime.fromisoformat(historical["inputAsOf"]) <= at
    before = deepcopy((bundle, historical))

    class ExportConnection:
        # Only frozen materials/history are exported. These adapter run times
        # bound the original validation clock; real DB metadata/authorization
        # are checked separately in the read-only API-role acceptance.
        async def fetchrow(self, query, *args):
            assert query.lstrip().upper().startswith("SELECT")
            if "alpha_public_batches" in query:
                assert str(args[0]) == historical["sourceBatchId"]
                return {
                    "id": UUID(historical["sourceBatchId"]),
                    "status": "PARTIAL",
                    "completed_at": datetime.fromisoformat(
                        historical["batchCompletedAt"]
                    ),
                    "payload": {"fundHistories": historical["histories"]},
                }
            assert str(args[0]) == RUN
            return {
                "run_id": UUID(RUN),
                "status": "FAILED",
                "dispatched_at": datetime.fromisoformat(historical["inputAsOf"]),
                "recorded_at": at + timedelta(seconds=1),
                "validated_at": at,
                "source_batch_id": UUID(historical["sourceBatchId"]),
                "error_class": "ResearchValidationError",
            }

        async def fetchval(self, query, *args):
            assert query.lstrip().upper().startswith("SELECT")
            assert "read_validation_artifacts_admin" in query and str(args[0]) == RUN
            return deepcopy(bundle)

        async def execute(self, *_args):
            raise AssertionError("Full audit preview must not write")

    result = await preview_research(
        ExportConnection(), UUID(RUN), now=at + timedelta(days=1)
    )
    assert result["readOnly"] is True and result["investmentEligible"] is False
    assert result["state"] == "PARTIAL" and result["sourceDigests"] == HASHES
    assert datetime.fromisoformat(result["validatedAt"]) == at
    assert (
        datetime.fromisoformat(
            result["opportunityContext"]["dailyResearch"]["completedAt"]
        )
        == at
    )
    assert {fund["fundCode"] for fund in result["funds"]} >= {
        "018344",
        "013477",
        "011035",
        "008298",
        "007466",
        "161129",
    }
    assert (
        len(result["funds"])
        == len({fund["fundCode"] for fund in result["funds"]})
        == 43
    )
    assert sum(fund["analysis"] is not None for fund in result["funds"]) == 41
    for fund in result["funds"]:
        assert fund["investmentEligible"] is False
        if fund["fundCode"] in {"024688", "028764"}:
            assert fund["anchor"] is None and fund["analysis"] is None
            assert "FROZEN_ANCHOR_MISSING" in fund["missingReasons"]
            continue
        assert not fund["missingReasons"]
        anchor = next(
            row
            for row in frozen.packet["discoveryActionableZones"]
            if row.get("fundCode") == fund["fundCode"]
        )
        series = next(
            row
            for row in historical["histories"]
            if row["fundCode"] == fund["fundCode"]
        )
        assert fund["anchor"]["currentPrice"] == anchor["currentPrice"]
        assert fund["anchor"]["orderPriceAvailable"] is False
        assert len(fund["analysis"]["points"]) == len(series["points"])
        if fund["fundCode"] in {"018344", "013477", "011035"}:
            assert len(series["points"]) == 550
        assert [
            (row["date"], Decimal(str(row["close"])))
            for row in fund["analysis"]["points"]
        ] == [(row["date"], Decimal(str(row["close"]))) for row in series["points"]]
        for original in anchor["tracks"]:
            shown = next(
                track
                for track in fund["anchor"]["tracks"]
                if track["track"] == original["track"]
            )
            for field in (
                "entryZoneMin",
                "entryZoneMax",
                "sellZoneMin",
                "sellZoneMax",
                "stopLossPrice",
                "takeProfitT1",
                "takeProfitT2",
            ):
                assert shown.get(field) == original.get(field)
    robot = next(
        row
        for row in result["report"]["valueDirections"]
        if row["key"] == "robotics_mobility"
    )
    assert (
        robot["signal"] == "WATCH"
        and robot["qualificationStatus"] == "OBSERVATION_ONLY"
    )
    assert robot["plans"] == [] and robot["investmentEligible"] is False
    assert (
        "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED" in robot["formalEligibilityReasons"]
    )
    assert (bundle, historical) == before

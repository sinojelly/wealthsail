"""Read priorities do not mutate frozen topics or grant investment permission."""

from copy import deepcopy
from importlib import import_module
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_research_topic_repository import registry
from tests.unit.test_research_acceptance_repository import NOW


def rank(items, accepted=None):
    return import_module("backend.macro.research_topic_priority").topic_highlights(
        items, accepted=accepted or {}
    )


def test_checked_source_topics_beyond_first_page_reach_top10_without_mutating_catalog():
    items = registry(uuid4(), 64)["items"]
    for item in items:
        item["evidenceCoverageScore"] = 0
    items[12]["evidenceCoverageScore"] = 50
    items[63]["evidenceCoverageScore"] = 100
    frozen = deepcopy(items)

    result = rank(items)

    assert [row["topic"]["key"] for row in result["items"][:2]] == [
        "topic-63",
        "topic-12",
    ]
    assert len(result["items"]) == 10 and result["catalogCount"] == 64
    assert result["items"][0]["priority"]["checkedSourceCoveragePercent"] == 100
    assert result["actionable"] is False and items == frozen


def test_explicit_high_importance_negative_pending_evidence_is_not_sentiment_demoted():
    items = registry(uuid4(), 14)["items"]
    items[0]["evidenceCoverageScore"] = 100
    items[13].update(title="融资断档与裁员风险", evidenceCoverageScore=0)
    accepted = {
        "events": [
            {
                "importance": "HIGH",
                "relatedTopicKeys": ["topic-13"],
                "title": "融资骤降",
                "sourceIds": ["source:risk"],
            }
        ]
    }

    result = rank(items, accepted)

    first = result["items"][0]
    assert first["topic"]["key"] == "topic-13"
    assert first["priority"]["importance"] == "HIGH"
    assert first["priority"]["checkedSourceCoveragePercent"] == 0
    assert first["topic"]["investmentEligible"] is False


def test_unknown_importance_and_evidence_stay_unknown_not_inferred_from_title():
    items = registry(uuid4(), 2)["items"]
    items[1]["title"] = "重大国家政策利好"
    result = rank(
        items,
        {
            "events": [
                {
                    "importance": "HIGH",
                    "relatedTopicKeys": ["unregistered"],
                    "affectedDirectionIds": ["topic-1"],
                }
            ]
        },
    )
    assert [row["topic"]["key"] for row in result["items"]] == ["topic-0", "topic-1"]
    assert all(row["priority"]["importance"] == "UNKNOWN" for row in result["items"])
    assert all(
        row["priority"]["checkedSourceCoveragePercent"] is None
        for row in result["items"]
    )


def test_progress_and_scope_only_break_equal_importance_and_evidence_then_stable_ordinal():
    items = registry(uuid4(), 4)["items"]
    items[2]["match"]["scopeVerified"] = True
    items[1]["status"] = "OBSERVATION_ONLY"
    result = rank(items)
    assert [row["topic"]["key"] for row in result["items"]] == [
        "topic-2",
        "topic-1",
        "topic-0",
        "topic-3",
    ]
    assert result == rank(deepcopy(items))


@pytest.mark.parametrize("count", [0, 1, 10, 12, 20, 30, 72])
def test_projection_is_bounded_for_full_valid_catalog(count):
    result = rank(registry(uuid4(), count)["items"])
    assert len(result["items"]) == min(count, 10)
    assert result["catalogCount"] == count


def test_capacity_excess_rejects_instead_of_silently_ignoring_topics():
    from backend.errors import ValidationError_

    with pytest.raises(ValidationError_):
        rank(registry(uuid4(), 73)["items"])


class CatalogConnection:
    def __init__(self, run_id, *, allowed=True):
        module = import_module("backend.macro.research_topic_repository")
        value = registry(run_id, 64)
        value["items"][63]["evidenceCoverageScore"] = 100
        self.safe = module.prepare_topic_registry(value, run_id, NOW)
        self.snapshot_id = uuid4()
        self.allowed = allowed
        self.reads = []

    async def fetchval(self, sql, *args):
        self.reads.append(sql)
        return self.allowed if "validation_live_member" in sql else 64

    async def fetchrow(self, sql, *args):
        self.reads.append(sql)
        return {
            "id": self.snapshot_id,
            "metadata": self.safe,
            "validated_at": NOW,
            "total_count": 64,
        }

    async def fetch(self, sql, *args):
        self.reads.append(sql)
        assert sql.lstrip().startswith("SELECT")
        items = [
            {"ordinal": index + 1, "body": item}
            for index, item in enumerate(self.safe["items"])
        ]
        return items if "LIMIT 73" in sql else items[:13]


@pytest.mark.asyncio
async def test_read_sidecar_prioritizes_whole_catalog_while_preserving_page_and_cursor(
    monkeypatch,
):
    module = import_module("backend.macro.research_topic_repository")
    run_id = uuid4()
    conn = CatalogConnection(run_id)
    summary = AsyncMock(
        return_value={
            "researchId": str(run_id),
            "validatedAt": NOW.isoformat(),
            "recordedAt": NOW.isoformat(),
            "kind": "INITIAL",
            "archiveState": "ARCHIVED",
            "accepted": {
                "events": [{"importance": "HIGH", "relatedTopicKeys": ["topic-13"]}]
            },
        }
    )
    monkeypatch.setattr(module.acceptance, "read_validation_summary", summary)
    normal = await module.read_topic_registry(conn, run_id, NOW)
    highlighted = await module.read_topic_registry(
        conn, run_id, NOW, include_highlights=True
    )
    assert normal["items"] == highlighted["items"]
    assert normal["nextCursor"] == highlighted["nextCursor"]
    assert "highlights" not in normal
    assert highlighted["highlights"]["items"][0]["topic"]["key"] == "topic-13"
    assert highlighted["highlights"]["items"][1]["topic"]["key"] == "topic-63"
    summary.assert_awaited_once_with(conn, run_id, NOW, admin=False)
    assert all(sql.lstrip().startswith("SELECT") for sql in conn.reads)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["wrong_run", "wrong_clock", "replay", "pending"])
async def test_highlight_event_importance_never_comes_from_other_run_clock_or_replay(
    monkeypatch, invalid
):
    module = import_module("backend.macro.research_topic_repository")
    run_id = uuid4()
    summary = {
        "researchId": str(run_id),
        "validatedAt": NOW.isoformat(),
        "recordedAt": NOW.isoformat(),
        "kind": "INITIAL",
        "archiveState": "ARCHIVED",
        "accepted": {
            "events": [{"importance": "HIGH", "relatedTopicKeys": ["topic-13"]}]
        },
    }
    if invalid == "wrong_run":
        summary["researchId"] = str(uuid4())
    elif invalid == "wrong_clock":
        summary["validatedAt"] = "2020-01-01T00:00:00+00:00"
    elif invalid == "replay":
        summary["kind"] = "REPLAY"
    else:
        summary["archiveState"] = "AUDIT_PENDING"
    monkeypatch.setattr(
        module.acceptance, "read_validation_summary", AsyncMock(return_value=summary)
    )
    result = await module.read_topic_registry(
        CatalogConnection(run_id), run_id, NOW, include_highlights=True
    )
    assert result["highlights"]["items"][0]["topic"]["key"] == "topic-63"
    assert result["highlights"]["items"][0]["priority"]["importance"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_highlights_enforce_same_live_member_access_before_reading_whole_catalog():
    from backend.errors import ForbiddenError

    module = import_module("backend.macro.research_topic_repository")
    run_id = uuid4()
    conn = CatalogConnection(run_id, allowed=False)
    with pytest.raises(ForbiddenError):
        await module.read_topic_registry(conn, run_id, NOW, include_highlights=True)
    assert len(conn.reads) == 1

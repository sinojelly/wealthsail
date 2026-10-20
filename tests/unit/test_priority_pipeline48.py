"""Exercise SQL packet assembly through final direction validation and ranking."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_daily_research import NOW, report
from test_opportunity_priority47 import plan
from test_value_directions import direction


@pytest.mark.asyncio
async def test_model_packet_keeps_identical_price_anchors_and_report_is_scored_after_validation(
    monkeypatch,
):
    from backend.jobs import daily_research as job
    from backend.investment import repository, service, discovery, theme_graph
    from backend.macro import market_repository, alpha_repository, alpha_research
    from backend.macro.daily_research import validate_report

    anchor = plan()
    anchor["signals"] = [
        {
            "track": "LEFT_VALUE",
            "triggerState": "CANDIDATE",
            "candidate": True,
            "entryReady": True,
            "inEntryZone": True,
            "distanceToEntryPct": 0,
        }
    ]
    scan = {
        "methodVersion": "fund-quant-scan-v2",
        "asOf": NOW.isoformat(),
        "minimumHistorySamples": 220,
        "items": [deepcopy(anchor)],
    }
    discovery_packet = {
        "discoveryActionableZones": [deepcopy(anchor)],
        "quantitativeScan": scan,
    }
    before = deepcopy(discovery_packet)
    monkeypatch.setattr(
        job,
        "get_settings",
        lambda: SimpleNamespace(opportunity_tenant_id=None, opportunity_user_id=None),
    )
    monkeypatch.setattr(service, "market_context", AsyncMock(return_value={}))
    monkeypatch.setattr(repository, "directions", AsyncMock(return_value=[]))
    for name, value in (
        ("read_permissions", {}),
        ("read_collection", {}),
        ("read_series", []),
    ):
        monkeypatch.setattr(market_repository, name, AsyncMock(return_value=value))
    monkeypatch.setattr(
        alpha_repository, "read_alpha_public", AsyncMock(return_value={})
    )
    monkeypatch.setattr(theme_graph, "read_themes", AsyncMock(return_value=[]))
    for name in ("read_daily_research", "read_previous_daily_research"):
        monkeypatch.setattr(job, name, AsyncMock(return_value={}))
    monkeypatch.setattr(job, "read_recent_public_research", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        job,
        "make_research_packet",
        lambda *args, **kwargs: {
            "asOf": NOW.isoformat(),
            "directions": [{"id": "a-share:broad"}],
        },
    )
    monkeypatch.setattr(alpha_research, "extend_public_packet", lambda *args: {})
    monkeypatch.setattr(
        discovery, "build_discovery_packet", AsyncMock(return_value=discovery_packet)
    )
    conn = AsyncMock()
    conn.fetch.return_value = []
    packet = await job.load_public_packet(conn, NOW, {})
    raw = report()
    proposal = direction(sourceIds=[], macroCodes=[], quantitativeFundCodes=["008281"])
    proposal["plans"][0]["subjectId"] = "008281"
    raw["valueDirections"] = [proposal]
    result = validate_report(raw, packet, {}, completed_at=NOW)
    actual = result["valueDirections"][0]["plans"][0]
    assert actual["currentPrice"] == anchor["currentPrice"]
    assert actual["tracks"][0]["entryZoneMin"] == anchor["tracks"][0]["entryZoneMin"]
    assert actual["opportunityScore"] == 57.5
    assert "opportunityScore" not in packet["quantitativeScan"]["items"][0]["tracks"][0]
    assert discovery_packet == before

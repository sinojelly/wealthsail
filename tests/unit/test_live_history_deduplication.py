"""Request-local history reuse compares complete cohorts, never just fund/date."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.investment import live_fund_plans as service
from tests.unit.test_live_fund_plans_v6 import LocalHistory, NOW, history, old_plan


@pytest.mark.asyncio
async def test_full_duplicate_cohorts_are_validated_once_but_revisions_and_clocks_are_kept(
    monkeypatch,
):
    base = history()
    clock = {**deepcopy(base), "retrievedAt": (NOW - timedelta(minutes=1)).isoformat()}
    revised = deepcopy(base)
    revised["points"][-1]["close"] = "1.2345"
    source = {**deepcopy(base), "sourceUri": "https://example.org/other-nav"}
    metadata = {**deepcopy(base), "providerRevision": "2026-10-28-correction"}
    supplied = [base, deepcopy(base), clock, revised, source, metadata]
    original = deepcopy(supplied)
    seen = []
    safe = service._safe_history

    def counted(row):
        seen.append(row)
        return safe(row)

    monkeypatch.setattr(service, "_safe_history", counted)
    result = await service.refresh_report_fund_plans(
        LocalHistory([base, deepcopy(base)]),
        {"actionablePlans": [old_plan()]},
        NOW,
        histories=supplied,
        packet={"fundHistories": [deepcopy(base)]},
        repair_nav=False,
    )
    assert len(seen) == 5
    assert seen == [base, clock, revised, source, metadata]
    assert supplied == original
    assert result["actionablePlans"][0]["currentPrice"] != "99.99"


def test_non_json_history_metadata_is_not_coerced_or_deduplicated():
    original = {**history(), "revision": Decimal("1.25")}
    result = list(service._distinct_histories([original, deepcopy(original)]))
    assert len(result) == 2 and isinstance(result[0]["revision"], Decimal)


def test_distinct_clocks_skip_full_comparison_and_same_clock_keeps_typed_revisions(monkeypatch):
    first = {**history(), "revision": 1}
    recent = {**first, "retrievedAt": (NOW - timedelta(minutes=1)).isoformat()}
    changed_type = {**first, "revision": True}
    reordered = dict(reversed(list(first.items())))
    compared = []
    identity = service._history_identity

    def observed(row):
        compared.append(id(row))
        return identity(row)

    monkeypatch.setattr(service, "_history_identity", observed)
    result = list(service._distinct_histories([first, recent, changed_type, reordered]))
    assert [id(row) for row in result] == [id(first), id(recent), id(changed_type)]
    assert compared == [id(first), id(changed_type), id(reordered)]


@pytest.mark.asyncio
async def test_authenticated_default_read_cannot_start_external_history_repair(
    monkeypatch,
):
    repair = AsyncMock(
        side_effect=AssertionError("external repair was not explicitly requested")
    )
    monkeypatch.setattr("backend.funds.nav_history.ensure_nav_histories", repair)
    monkeypatch.setattr(
        "backend.investment.research_fund_references.resolve_report_fund_references",
        AsyncMock(side_effect=lambda _c, value, *_a: value),
    )
    result = await service.refresh_report_fund_plans(
        LocalHistory([history()]),
        {"actionablePlans": [old_plan()]},
        NOW,
        tenant_id="tenant",
        user_id="actor",
    )
    assert result["actionablePlans"][0]["currentPrice"] != "99.99"
    assert repair.await_count == 0

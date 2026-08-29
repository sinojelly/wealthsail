"""Business rules for derived import-draft freshness and actions."""

from __future__ import annotations

from datetime import datetime

import pytest

from backend.portfolio.imports.freshness import (
    available_actions,
    evaluate_freshness,
)


SOURCE_AS_OF = datetime.fromisoformat("2026-08-28T08:00:00+08:00")


def _freshness(**overrides: object):
    values = {
        "stored_account_hash": "facts-v1",
        "current_account_hash": "facts-v1",
        "stored_engine_version": "tiantian-preview/3.0.0",
        "current_engine_version": "tiantian-preview/3.0.0",
        "source_as_of": SOURCE_AS_OF,
        "latest_confirmed_source_as_of": None,
    }
    values.update(overrides)
    return evaluate_freshness(**values)


def test_unchanged_preview_context_is_fresh() -> None:
    result = _freshness()

    assert result.freshness == "FRESH"
    assert result.reasons == ()


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"current_account_hash": "facts-v2"}, "ACCOUNT_FACTS_CHANGED"),
        (
            {"current_engine_version": "tiantian-preview/3.1.0"},
            "PREVIEW_ENGINE_CHANGED",
        ),
        (
            {
                "latest_confirmed_source_as_of": datetime.fromisoformat(
                    "2026-08-29T08:00:00+08:00"
                )
            },
            "NEWER_SOURCE_SNAPSHOT_CONFIRMED",
        ),
    ],
)
def test_each_changed_preview_dependency_makes_the_draft_stale(
    overrides: dict[str, object], reason: str
) -> None:
    result = _freshness(**overrides)

    assert result.freshness == "STALE"
    assert result.reasons == (reason,)


def test_reasons_have_stable_order_when_multiple_dependencies_changed() -> None:
    result = _freshness(
        current_account_hash="facts-v2",
        current_engine_version="tiantian-preview/3.1.0",
        latest_confirmed_source_as_of=datetime.fromisoformat(
            "2026-08-29T08:00:00+08:00"
        ),
    )

    assert result.reasons == (
        "ACCOUNT_FACTS_CHANGED",
        "PREVIEW_ENGINE_CHANGED",
        "NEWER_SOURCE_SNAPSHOT_CONFIRMED",
    )


def test_fresh_valid_draft_exposes_all_safe_actions() -> None:
    assert available_actions(
        status="DRAFT",
        validation_status="VALID",
        freshness="FRESH",
        binding_status="ACTIVE",
    ) == ("VIEW", "CONFIRM", "CANCEL", "REPARSE")


def test_stale_draft_cannot_confirm_but_can_be_reparsed_or_cancelled() -> None:
    assert available_actions(
        status="DRAFT",
        validation_status="VALID",
        freshness="STALE",
        binding_status="ACTIVE",
    ) == ("VIEW", "CANCEL", "REPARSE")


def test_disabled_binding_is_a_blocking_action_not_a_freshness_reason() -> None:
    assert available_actions(
        status="DRAFT",
        validation_status="VALID",
        freshness="FRESH",
        binding_status="DISABLED",
    ) == ("VIEW", "CANCEL", "REPARSE")


def test_terminal_batch_is_read_only() -> None:
    assert available_actions(
        status="CONFIRMED",
        validation_status="VALID",
        freshness="FRESH",
        binding_status="ACTIVE",
    ) == ("VIEW",)

"""Pure governance rules for recoverable portfolio-import drafts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Freshness = Literal["FRESH", "STALE"]
FreshnessReason = Literal[
    "ACCOUNT_FACTS_CHANGED",
    "PREVIEW_ENGINE_CHANGED",
    "NEWER_SOURCE_SNAPSHOT_CONFIRMED",
]
AvailableAction = Literal["VIEW", "CONFIRM", "CANCEL", "REPARSE"]

# This version covers parsing, normalization, master-data matching, comparison,
# source-history classification and holding-start inference.  Bump it whenever
# any of those rules can change a persisted preview.
PREVIEW_ENGINE_VERSION = "tiantian-preview/3.0.0"


@dataclass(frozen=True, slots=True)
class FreshnessResult:
    freshness: Freshness
    reasons: tuple[FreshnessReason, ...]


def evaluate_freshness(
    *,
    stored_account_hash: str,
    current_account_hash: str,
    stored_engine_version: str,
    current_engine_version: str,
    source_as_of: datetime,
    latest_confirmed_source_as_of: datetime | None,
) -> FreshnessResult:
    """Compare only deterministic dependencies captured by a DRAFT preview."""
    reasons: list[FreshnessReason] = []
    if stored_account_hash != current_account_hash:
        reasons.append("ACCOUNT_FACTS_CHANGED")
    if stored_engine_version != current_engine_version:
        reasons.append("PREVIEW_ENGINE_CHANGED")
    if (
        latest_confirmed_source_as_of is not None
        and latest_confirmed_source_as_of > source_as_of
    ):
        reasons.append("NEWER_SOURCE_SNAPSHOT_CONFIRMED")
    return FreshnessResult(
        freshness="STALE" if reasons else "FRESH",
        reasons=tuple(reasons),
    )


def available_actions(
    *,
    status: str,
    validation_status: str,
    freshness: Freshness,
    binding_status: str | None,
) -> tuple[AvailableAction, ...]:
    """Return user actions without conflating validation, binding and freshness."""
    if status != "DRAFT":
        return ("VIEW",)
    actions: list[AvailableAction] = ["VIEW"]
    if (
        validation_status == "VALID"
        and freshness == "FRESH"
        and binding_status in {"PENDING", "ACTIVE"}
    ):
        actions.append("CONFIRM")
    actions.extend(("CANCEL", "REPARSE"))
    return tuple(actions)

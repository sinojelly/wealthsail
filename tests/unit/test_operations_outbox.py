"""Operations projection exposes every persisted Outbox terminal/hold state."""

from __future__ import annotations

import pytest

from backend.operations.repository import outbox_status


class _Connection:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, _sql: str, *_args):
        self.calls += 1
        if self.calls == 1:
            return [
                {"status": "PENDING", "count": 1},
                {"status": "UNKNOWN", "count": 2},
                {"status": "CANCELLED", "count": 3},
            ]
        return []


@pytest.mark.asyncio
async def test_outbox_counts_include_manual_reconciliation_states() -> None:
    result = await outbox_status(_Connection(), "tenant-a")

    assert result["counts"] == {
        "pending": 1,
        "sending": 0,
        "sent": 0,
        "unknown": 2,
        "dead": 0,
        "cancelled": 3,
    }

"""Complete provider responses remain bound to their disclosed NAV coordinates."""

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json

import httpx
import pytest

from backend.integrations.market_data import fetch_fund_nav_history


def response(page, rows, *, total=3, size=2):
    return httpx.Response(
        200,
        json={
            "PageIndex": page,
            "PageSize": size,
            "TotalCount": total,
            "Data": {"LSJZList": rows},
        },
    )


def nav(day, value):
    return {"FSRQ": f"2026-10-{day:02d}", "DWJZ": value}


async def fetch(responses):
    async def serve(request):
        return responses.pop(0)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        return await fetch_fund_nav_history(
            "162411", date(2026, 10, 1), date(2026, 10, 19), client
        )


@pytest.mark.asyncio
async def test_nav_history_retains_published_daily_return_including_distribution_days():
    rows = await fetch(
        [
            response(
                1,
                [
                    {**nav(16, "0.95"), "JZZZL": "1.25"},
                    {**nav(15, "1"), "JZZZL": "0.00"},
                ],
                total=2,
            ),
        ]
    )
    assert rows[-1].metadata.get("returnPct") == "1.25"
    assert rows[0].metadata.get("returnPct") == "0.00"


@pytest.mark.asyncio
async def test_complete_paginated_nav_response_proves_exact_returned_coordinates():
    rows = await fetch(
        [response(1, [nav(16, "1.0200"), nav(15, "1.01")]), response(2, [nav(14, "1")])]
    )
    proof = rows[0].metadata.get("providerHistoryProof")
    assert proof is not None
    assert proof == {
        **proof,
        "methodVersion": "eastmoney-nav-history-v1",
        "fundCode": "162411",
        "sourceUri": "https://api.fund.eastmoney.com/f10/lsjz",
        "queryStart": "2026-10-01",
        "queryEnd": "2026-10-19",
        "totalCount": 3,
        "receivedRowCount": 3,
        "uniquePointCount": 3,
        "pointCount": 3,
        "pageCount": 2,
        "requiredPages": 2,
        "complete": True,
        "firstDate": "2026-10-14",
        "lastDate": "2026-10-16",
    }
    expected = hashlib.sha256(
        json.dumps(
            [["2026-10-14", "1"], ["2026-10-15", "1.01"], ["2026-10-16", "1.02"]],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert proof["pointsDigest"] == expected
    assert all(row.metadata["providerHistoryProof"] is proof for row in rows)
    assert proof["retrievedAt"].endswith("+00:00")


@pytest.mark.asyncio
async def test_matching_page_boundary_duplicate_is_complete_without_fabricating_points():
    rows = await fetch(
        [
            response(1, [nav(16, "1.02"), nav(15, "1.0100")], total=4),
            response(2, [nav(15, "1.01"), nav(14, "1")], total=4),
        ]
    )
    assert len(rows) == 3
    proof = rows[0].metadata.get("providerHistoryProof")
    assert proof and proof["receivedRowCount"] == 4
    assert proof["uniquePointCount"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [nav(15, "9.99"), nav(15, "NaN"), {"FSRQ": "garbage", "DWJZ": "1"}, nav(30, "1")],
)
async def test_conflicting_or_invalid_provider_rows_never_certify_complete_history(bad):
    rows = await fetch(
        [
            response(1, [nav(16, "1.02"), nav(15, "1.01")], total=4),
            response(2, [bad, nav(14, "1")], total=4),
        ]
    )
    assert rows
    assert all("providerHistoryProof" not in row.metadata for row in rows)


@pytest.mark.asyncio
async def test_extra_rows_beyond_provider_total_cannot_certify_a_snapshot():
    rows = await fetch([response(1, [nav(16, "1.02"), nav(15, "1.01")], total=1)])
    assert all("providerHistoryProof" not in row.metadata for row in rows)


def test_digest_ignores_decimal_notation_and_binds_the_actual_ordered_dates():
    from backend.integrations.nav_history_proof import nav_points_digest

    a = [
        {"date": "2026-10-15", "close": Decimal("1.0100")},
        {"date": "2026-10-16", "close": "1E0"},
    ]
    b = [{"date": "2026-10-15", "close": "1.01"}, {"date": "2026-10-16", "close": 1}]
    assert nav_points_digest(a) == nav_points_digest(b)
    assert nav_points_digest(a) != nav_points_digest(list(reversed(b)))


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "0", "-1", True, None])
def test_digest_rejects_invalid_unit_nav(bad):
    from backend.integrations.nav_history_proof import nav_points_digest

    with pytest.raises(ValueError):
        nav_points_digest([{"date": "2026-10-15", "close": bad}])


@pytest.mark.asyncio
async def test_history_proof_binds_the_retained_500_points_without_inventing_more_history():
    start = date(2024, 1, 1)
    raw = [
        {
            "FSRQ": (start + timedelta(days=i)).isoformat(),
            "DWJZ": str(Decimal("1") + Decimal(i) / 10000),
        }
        for i in range(600)
    ]

    async def serve(request):
        index = int(request.url.params["pageIndex"])
        return response(
            index, raw[(index - 1) * 100 : index * 100], total=600, size=100
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        rows = await fetch_fund_nav_history(
            "162411", start, start + timedelta(days=599), client
        )
    proof = rows[-1].metadata["providerHistoryProof"]
    assert len(rows) == proof["uniquePointCount"] == 600
    assert proof["pointCount"] == 500
    assert proof["firstDate"] == (start + timedelta(days=100)).isoformat()
    from backend.integrations.nav_history_proof import nav_points_digest

    assert proof["pointsDigest"] == nav_points_digest(
        [{"date": r.as_of.date().isoformat(), "close": r.value} for r in rows[-500:]]
    )

"""Subscription constraints follow the actual fund into report research prices."""

from copy import deepcopy
from datetime import timedelta
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from backend.investment import discovery
from backend.jobs import opportunity_market
from test_phase2_research import NOW, anchor


@pytest.mark.parametrize(
    "purchase,limit,restricted,label",
    [
        ("SUSPENDED", None, True, "⛔ 暂停申购"),
        ("LIMITED", "10", True, "⛔ 单日限购10元"),
        ("LIMITED", "100", True, "⛔ 单日限购100元"),
        ("OPEN", "0", True, "⛔ 单日限购0元"),
        ("LIMITED", "100.01", False, None),
        ("LIMITED", "10000", False, None),
        ("LIMITED", "NaN", False, None),
        ("LIMITED", True, False, None),
        ("OPEN", None, False, None),
    ],
)
@pytest.mark.asyncio
async def test_small_confirmed_limit_or_suspension_blocks_primary_entry(
    purchase, limit, restricted, label
):
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "fund_code": "161129",
            "purchase_status": purchase,
            "daily_purchase_limit": limit,
            "retrieved_at": NOW,
        }
    ]
    rows = await opportunity_market.read_fund_subscription_status(conn, NOW, ["161129"])
    assert rows["161129"]["restricted"] is restricted
    assert rows["161129"]["label"] == label
    if limit == "10":
        assert rows["161129"]["dailyLimitCny"] == "10"


@pytest.mark.asyncio
async def test_subscription_read_rejects_future_unrequested_and_unknown_status():
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "fund_code": "161129",
            "purchase_status": "SUSPENDED",
            "source_metadata": {},
            "retrieved_at": NOW + timedelta(seconds=1),
        },
        {
            "fund_code": "162411",
            "purchase_status": "SUSPENDED",
            "source_metadata": {},
            "retrieved_at": NOW,
        },
        {
            "fund_code": "270042",
            "purchase_status": "UNKNOWN",
            "source_metadata": {},
            "retrieved_at": NOW,
        },
    ]
    rows = await opportunity_market.read_fund_subscription_status(
        conn, NOW, ["161129", "270042"]
    )
    assert rows == {}
    sql, codes, observed_at = conn.fetch.await_args.args
    assert "reference.fund_subscription_status" in sql
    assert "effective_from" in sql and "effective_to" in sql
    assert "retrieved_at" in sql
    assert codes == ["161129", "270042"] and observed_at == NOW


@pytest.mark.parametrize("purchase", [None, [], {}, True, "UNKNOWN"])
def test_unrecognized_subscription_payload_never_claims_openness(purchase):
    assert (
        opportunity_market.normalize_subscription_status(
            {"purchaseStatus": purchase, "dailyLimitCny": "10"}
        )
        is None
    )


@pytest.mark.asyncio
async def test_loaded_status_reaches_universe_anchor_and_quantitative_total(
    monkeypatch,
):
    product = {
        "subjectKind": "nav",
        "subjectId": "161129",
        "fundCode": "161129",
        "market": "FUND_CN",
        "label": "易方达原油",
        "overseasCalendarRequired": True,
    }
    nav = anchor()
    nav.update(subjectId="161129", fundCode="161129", historyQuality="READY")
    before = deepcopy(nav)
    conn = AsyncMock()
    conn.fetch.return_value = [
        {
            "fund_code": "161129",
            "purchase_status": "LIMITED",
            "daily_purchase_limit": "10",
            "retrieved_at": NOW,
        }
    ]
    monkeypatch.setattr(
        discovery, "select_research_assets", AsyncMock(return_value=[product])
    )
    monkeypatch.setattr(discovery, "_cached_histories", AsyncMock(return_value={}))
    result = await discovery.build_discovery_packet(
        conn, {"themeActionableZones": [nav]}, {}, [], {}, NOW
    )
    for row in (
        result["discoveryUniverse"][0],
        result["discoveryActionableZones"][0],
        result["quantitativeScan"]["items"][0],
    ):
        assert row["subscriptionRestricted"] is True
        assert row["subscriptionLabel"] == "⛔ 单日限购10元"
        assert row["subscriptionStatus"]["dailyLimitCny"] == "10"
    assert result["fundSubscriptionStatus"]["161129"]["restricted"] is True
    assert nav == before


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("INVESTMENT_TEST_DATABASE_URL"),
    reason="isolated validation database required",
)
async def test_real_worker_reads_subscription_view_without_underlying_table_access():
    conn = await asyncpg.connect(os.environ["INVESTMENT_TEST_DATABASE_URL"])
    tx = conn.transaction()
    await tx.start()
    try:
        instrument, code = uuid4(), f"{900000 + uuid4().int % 100000:06}"
        await conn.execute(
            """INSERT INTO reference.instruments
              (id,asset_type,primary_market,canonical_name)
              VALUES($1,'OFF_EXCHANGE_FUND','FUND_CN','subscription fixture')""",
            instrument,
        )
        await conn.execute(
            """INSERT INTO reference.instrument_aliases
              (instrument_id,alias_type,alias_value,market,effective_from)
              VALUES($1,'TICKER',$2,'FUND_CN',$3)""",
            instrument,
            code,
            NOW.date(),
        )
        await conn.execute(
            """INSERT INTO reference.fund_profiles
              (instrument_id,purchase_status,effective_from,source_metadata,retrieved_at)
              VALUES($1,'LIMITED',$2,'{"dailyPurchaseLimit":"10"}'::jsonb,$3)""",
            instrument,
            NOW.date(),
            NOW,
        )
        await conn.execute("SET LOCAL ROLE wealthsail_worker")
        assert not await conn.fetchval(
            "SELECT has_table_privilege(current_user,'reference.fund_profiles','SELECT')"
        )
        assert not await conn.fetchval(
            "SELECT has_table_privilege(current_user,'reference.fund_subscription_status','UPDATE')"
        )
        status = await opportunity_market.read_fund_subscription_status(
            conn, NOW, [code]
        )
        assert status[code]["label"] == "⛔ 单日限购10元"
    finally:
        await tx.rollback()
        await conn.close()

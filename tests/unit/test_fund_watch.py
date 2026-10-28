"""Owned holdings join personal tracking without changing portfolio facts."""

from datetime import datetime
from importlib import import_module, util
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 28, 10, tzinfo=ZoneInfo("Asia/Shanghai"))


def module():
    assert util.find_spec("backend.investment.fund_watch"), "fund watch missing"
    return import_module("backend.investment.fund_watch")


@pytest.mark.asyncio
async def test_watch_context_includes_all_owned_funds_and_persistent_suppression(
    monkeypatch,
):
    from backend.investment import discovery

    instrument = uuid4()
    conn = AsyncMock()
    conn.fetch.side_effect = [
        [
            {
                "instrument_id": instrument,
                "fund_code": "004069",
                "market": "FUND_CN",
                "fund_name": "证券联接",
            }
        ],
        [{"fund_key": "004069", "fund_market": "FUND_CN"}],
    ]
    monkeypatch.setattr(discovery, "_cached_histories", AsyncMock(return_value={}))
    data = await module().list_watch_context(conn, "tenant", "actor", NOW)

    assert data == {
        "heldFunds": [
            {
                "instrumentId": str(instrument),
                "fundCode": "004069",
                "fundMarket": "FUND_CN",
                "label": "证券联接",
                "confirmedNav": None,
            }
        ],
        "suppressedFundKeys": ["FUND_CN:004069"],
    }
    query, *args = conn.fetch.await_args_list[0].args
    assert args == ["tenant", "actor", NOW]
    assert "owner.user_id=$2" in query
    assert "BENEFICIAL_OWNER" in query and "JOINT_OWNER" in query
    assert "effective_from" in query and "effective_to" in query
    assert "DISTINCT ON(p.account_id,p.instrument_id)" in query
    assert "p.quantity>0" in query
    assert "created_at<=$3" in query
    assert "LIMIT $" not in query  # Holdings must not truncate at the study page size.
    assert conn.execute.await_count == 0


@pytest.mark.asyncio
async def test_watch_context_confirmed_nav_uses_same_fund_and_disclosed_daily_change(
    monkeypatch,
):
    from backend.investment import discovery

    conn = AsyncMock()
    conn.fetch.side_effect = [
        [
            {
                "instrument_id": uuid4(),
                "fund_code": "004069",
                "market": "FUND_CN",
                "fund_name": "证券联接",
            }
        ],
        [],
    ]
    history = {
        "fundCode": "004069",
        "asOf": "2026-10-27",
        "retrievedAt": NOW.isoformat(),
        "availableAt": NOW.isoformat(),
        "sourceUri": "https://fund.eastmoney.com/004069.html",
        "priceBasis": "UNIT_NAV",
        "points": [
            {"date": "2026-10-26", "close": "1.10"},
            {"date": "2026-10-27", "close": "1.0876", "returnPct": "0.25"},
        ],
        "calendarProof": {
            "market": "SSE",
            "checkedThrough": "2026-10-28",
            "days": [
                {"date": f"2026-10-{day}", "isTradingDay": True} for day in (26, 27, 28)
            ],
        },
    }
    monkeypatch.setattr(
        discovery, "_cached_histories", AsyncMock(return_value={"004069": history})
    )
    data = await module().list_watch_context(conn, "tenant", "actor", NOW)
    quote = data["heldFunds"][0]["confirmedNav"]
    assert quote["value"] == "1.0876"
    assert quote["asOf"] == "2026-10-27"
    assert quote["changePct"] == "0.25"
    assert quote["fundCode"] == "004069"
    assert quote["usable"] is True


@pytest.mark.asyncio
async def test_cancel_persists_actor_preference_and_stops_matching_studies_without_deleting_holdings(
    monkeypatch,
):
    from backend.investment import study_repository as repo

    conn = AsyncMock()
    track = {
        "id": uuid4(),
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled": True,
        "generation": 2,
    }
    conn.fetch.return_value = [track]
    monkeypatch.setattr(
        repo,
        "advance",
        AsyncMock(return_value={**track, "enabled": False, "generation": 3}),
    )
    monkeypatch.setattr(repo, "latest_version", AsyncMock(return_value={"id": uuid4()}))
    monkeypatch.setattr(repo, "add_note", AsyncMock())
    data = await module().set_watch_status(
        conn, "tenant", "actor", "004069", "FUND_CN", False, NOW
    )

    assert data == {"fundCode": "004069", "fundMarket": "FUND_CN", "enabled": False}
    query, *args = conn.fetch.await_args.args
    assert "t.tenant_id=$1" in query and "t.user_id=$2" in query
    assert "instrumentId" in query and "reference.instrument_aliases" in query
    assert "FOR UPDATE" in query
    assert args == ["tenant", "actor", "004069", "FUND_CN", None]
    writes = [call[0] for call in conn.mock_calls if call[0] in {"fetch", "execute"}]
    assert writes == ["fetch", "execute"], (
        "lock studies before the preference, matching resume lock order"
    )
    assert repo.advance.await_args.kwargs["enabled"] is False
    assert repo.add_note.await_args.args[4]["operation"] == "STOPPED"
    commands = " ".join(
        str(call.args[0]).upper() for call in conn.execute.await_args_list
    )
    assert "INSERT INTO RESEARCH.FUND_WATCH_PREFERENCES" in commands
    assert "DELETE" not in commands and "LEDGER." not in commands


@pytest.mark.asyncio
async def test_explicit_retrack_resumes_deduplicated_study_and_clears_suppression(
    monkeypatch,
):
    from backend.investment import study_repository as repo, study_service as service

    conn = AsyncMock()
    track = {
        "id": uuid4(),
        "tenant_id": "tenant",
        "user_id": "actor",
        "enabled": False,
        "generation": 2,
        "scope": {"fundCode": "004069", "fundMarket": "FUND_CN"},
    }
    monkeypatch.setattr(
        service, "validate_scope", AsyncMock(return_value=track["scope"])
    )
    monkeypatch.setattr(
        repo, "daily_origin", AsyncMock(return_value={"originType": "DAILY_EVENT"})
    )
    monkeypatch.setattr(repo, "lock_track", AsyncMock(return_value=track))
    monkeypatch.setattr(
        repo,
        "advance",
        AsyncMock(return_value={**track, "enabled": True, "generation": 3}),
    )
    monkeypatch.setattr(repo, "latest_version", AsyncMock(return_value={"id": uuid4()}))
    monkeypatch.setattr(repo, "add_note", AsyncMock())
    monkeypatch.setattr(
        service, "get_detail", AsyncMock(return_value={"track": {"enabled": True}})
    )
    conn.fetchval.return_value = track["id"]
    result = await service.create_study(
        conn,
        "tenant",
        "actor",
        {
            "originType": "DAILY_EVENT",
            "reportRunId": str(uuid4()),
            "eventId": "event",
            "scope": track["scope"],
        },
        NOW,
    )

    assert result["track"]["enabled"] is True
    assert repo.advance.await_count == 1, (
        "explicit re-track must resume an existing stopped origin"
    )
    assert repo.advance.await_args.kwargs["enabled"] is True
    assert any(
        "fund_watch_preferences" in call.args[0]
        for call in conn.execute.await_args_list
    )


def test_fund_watch_migration_uses_actor_rls_and_scoped_unique_preference():
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/092_fund_watch_preferences.sql"
    )
    assert path.exists(), "persistent fund preference migration missing"
    sql = path.read_text(encoding="utf-8")
    assert "PRIMARY KEY(tenant_id,user_id,fund_market,fund_key)" in sql
    assert "instrument_id UUID REFERENCES reference.instruments(id)" in sql
    assert (
        "CHECK(fund_key=COALESCE(fund_code,'instrument:'||instrument_id::text))" in sql
    )
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert sql.count("user_id=auth.uid()") >= 4
    assert "identity.is_tenant_member(tenant_id)" in sql
    assert "GRANT DELETE" not in sql


def test_fund_watch_status_command_is_registered_and_strict():
    api = (
        import_module("backend.investment.fund_watch_api")
        if util.find_spec("backend.investment.fund_watch_api")
        else None
    )
    assert api is not None, "fund watch status API missing"
    assert any(
        route.path == "/investment/fund-watch/status" and "POST" in route.methods
        for route in api.router.routes
    )
    assert (
        api.FundWatchStatusRequest.model_validate(
            {"fundCode": "004069", "enabled": False}
        ).fund_market
        == "FUND_CN"
    )
    with pytest.raises(ValueError):
        api.FundWatchStatusRequest.model_validate(
            {"fundCode": "004069", "enabled": "false"}
        )
    with pytest.raises(ValueError):
        api.FundWatchStatusRequest.model_validate({"enabled": False})
    instrument = uuid4()
    assert (
        api.FundWatchStatusRequest.model_validate(
            {"instrumentId": str(instrument), "enabled": False}
        ).instrument_id
        == instrument
    )
    main = (Path(__file__).resolve().parents[2] / "backend/main.py").read_text(
        encoding="utf-8"
    )
    assert "include_router(fund_watch_router" in main


@pytest.mark.asyncio
async def test_inactive_and_unaliased_held_funds_remain_visible(monkeypatch):
    from backend.investment import discovery

    conn = AsyncMock()
    missing = uuid4()
    rows = [
        {
            "instrument_id": uuid4(),
            "fund_code": "004069",
            "market": "FUND_CN",
            "fund_name": "暂停申购基金",
        },
        {
            "instrument_id": missing,
            "fund_code": None,
            "market": "FUND_CN",
            "fund_name": "清算中基金",
        },
    ]
    conn.fetch.side_effect = [
        rows,
        [{"fund_key": f"instrument:{missing}", "fund_market": "FUND_CN"}],
    ]
    histories = AsyncMock(return_value={})
    monkeypatch.setattr(discovery, "_cached_histories", histories)
    data = await module().list_watch_context(conn, "tenant", "actor", NOW)

    assert [row["label"] for row in data["heldFunds"]] == ["暂停申购基金", "清算中基金"]
    assert data["heldFunds"][1]["fundCode"] is None
    assert data["suppressedFundKeys"] == [f"FUND_CN:instrument:{missing}"]
    assert histories.await_args.args[1] == ["004069"]
    query = conn.fetch.await_args_list[0].args[0]
    assert "JOIN reference.instruments" in query
    assert "LEFT JOIN LATERAL" in query
    assert "lifecycle_status=" not in query
    assert (
        "alias.effective_to" in query
    )  # Prefer current identity, fall back to recorded former ticker.


@pytest.mark.asyncio
async def test_fund_without_ticker_can_be_canceled_by_instrument_identity():
    conn = AsyncMock()
    conn.fetch.return_value = []
    instrument = uuid4()
    data = await module().set_watch_status(
        conn,
        "tenant",
        "actor",
        None,
        "FUND_CN",
        False,
        NOW,
        instrument_id=instrument,
    )

    assert data == {
        "instrumentId": str(instrument),
        "fundMarket": "FUND_CN",
        "enabled": False,
    }
    assert conn.fetch.await_args.args[-1] == str(instrument)
    assert "scope->>'instrumentId'=$5" in conn.fetch.await_args.args[0]
    assert f"instrument:{instrument}" in conn.execute.await_args.args

import pytest

from contextlib import asynccontextmanager
import json
from unittest.mock import AsyncMock
from uuid import UUID


def test_reference_import_requires_evidence_and_does_not_guess_tracking_links():
    from scripts.register_investment_reference import ReferenceImport
    with pytest.raises(ValueError):
        ReferenceImport.model_validate({"indices": [{"id": "hs300", "providerSymbol": "sh000300"}]})
    data = ReferenceImport.model_validate({"indices": [{"id": "hs300", "providerSymbol": "sh000300", "sourceUri": "https://example.org/index/000300", "publishHour": 18}], "links": []})
    assert data.indices[0].publish_hour == 18
    assert data.links == []


def test_fund_source_registration_defaults_to_unverified_return_semantics():
    from scripts.register_investment_reference import ReferenceImport

    data = ReferenceImport.model_validate({"funds": [{
        "instrumentId": "00000000-0000-0000-0000-000000000001",
        "sourceUri": "https://fund.example.org/000001",
        "publishHour": 23,
    }]})
    assert data.funds[0].source_verified is True
    assert data.funds[0].return_series_verified is False
    assert data.links == []


@pytest.mark.parametrize("extra", [{"publishHour": True}, {"sourceVerified": "true"},
                                    {"returnSeriesVerified": "false"}])
def test_fund_source_registration_rejects_ambiguous_verification_config(extra):
    from scripts.register_investment_reference import ReferenceImport

    with pytest.raises(ValueError):
        ReferenceImport.model_validate({"funds": [{
            "instrumentId": "00000000-0000-0000-0000-000000000001",
            "sourceUri": "https://fund.example.org/000001",
            "publishHour": 23,
            **extra,
        }]})


@pytest.mark.asyncio
async def test_register_fund_source_merges_only_research_metadata(monkeypatch):
    from scripts import register_investment_reference as registry

    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": UUID("00000000-0000-0000-0000-000000000001")}

    @asynccontextmanager
    async def connection(pool):
        yield conn

    @asynccontextmanager
    async def transaction():
        yield

    conn.transaction = transaction
    monkeypatch.setattr(registry, "system_connection", connection)
    data = registry.ReferenceImport.model_validate({"funds": [{
        "instrumentId": "00000000-0000-0000-0000-000000000001",
        "sourceUri": "https://fund.example.org/000001",
        "publishHour": 23,
        "returnSeriesVerified": True,
    }]})
    await registry.register(object(), data)
    sql, instrument_id, payload = conn.fetchrow.await_args.args
    assert instrument_id == UUID("00000000-0000-0000-0000-000000000001")
    assert "jsonb_set" in sql and "metadata->'research'" in sql
    assert "OFF_EXCHANGE_FUND" in sql and "EXCHANGE_FUND" in sql
    config = json.loads(payload)
    assert config["sourceUri"] == "https://fund.example.org/000001"
    assert config["sourceVerified"] is True
    assert config["returnSeriesVerified"] is True
    assert config["publishHour"] == 23
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_unknown_fund_source_fails_instead_of_creating_identity(monkeypatch):
    from scripts import register_investment_reference as registry

    conn = AsyncMock()
    conn.fetchrow.return_value = None

    @asynccontextmanager
    async def connection(pool):
        yield conn

    @asynccontextmanager
    async def transaction():
        yield

    conn.transaction = transaction
    monkeypatch.setattr(registry, "system_connection", connection)
    data = registry.ReferenceImport.model_validate({"funds": [{
        "instrumentId": "00000000-0000-0000-0000-000000000001",
        "sourceUri": "https://fund.example.org/000001",
        "publishHour": 23,
    }]})
    with pytest.raises(ValueError, match="unknown research fund"):
        await registry.register(object(), data)

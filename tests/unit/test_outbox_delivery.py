"""Outbox and WeCom delivery regression tests (no network or database required)."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest

from backend.common.security import envelope_encrypt
from backend.config import Settings
from backend.integrations import wecom, wecom_bot
from backend.jobs.outbox import build_delivery_command, claim_batch
from backend.reports.repository import insert_report_outbox_deliveries


class _Transaction:
    def __init__(self, conn: "_ClaimConnection") -> None:
        self.conn = conn

    async def __aenter__(self):
        self.conn.in_transaction = True

    async def __aexit__(self, *_):
        self.conn.in_transaction = False


class _ClaimConnection:
    def __init__(self) -> None:
        self.in_transaction = False
        self.sql = ""
        self.args = ()

    def transaction(self):
        return _Transaction(self)

    async def fetch(self, sql: str, *args):
        assert self.in_transaction is True
        self.sql = sql
        self.args = args
        return [{"id": "outbox-1", "claim_token": "lease-1"}]


class _Pool:
    def __init__(self) -> None:
        self.conn = _ClaimConnection()

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


@pytest.mark.asyncio
async def test_claim_batch_is_one_atomic_update_returning_lease() -> None:
    """Two workers must compete on one UPDATE, not a lock-free SELECT then UPDATE."""
    pool = _Pool()

    rows = await claim_batch(pool, worker_id="worker-a", limit=20, lease_seconds=300)

    assert rows[0]["claim_token"] == "lease-1"
    sql = " ".join(pool.conn.sql.split()).upper()
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "UPDATE WORKFLOW.OUTBOX" in sql
    assert "RETURNING" in sql


def test_delivery_command_resolves_only_active_same_tenant_ciphertext() -> None:
    """The generic payload cannot select a recipient or cross a tenant boundary."""
    key = "routing-key-with-at-least-32-bytes!"
    binding = {
        "id": "binding-1",
        "tenant_id": "tenant-a",
        "channel_type": "wecom",
        "status": "ACTIVE",
        "external_user_ciphertext": envelope_encrypt(key, "wecom-user-7"),
    }
    row = {
        "id": "outbox-1",
        "tenant_id": "tenant-a",
        "channel_binding_id": "binding-1",
        "topic": "REPORT_PUBLISHED",
        "payload": {"touser": "attacker-selected", "session": "PRE_MARKET"},
    }

    command = build_delivery_command(row, binding, routing_key=key)

    assert command.recipient == "wecom-user-7"
    assert command.channel_type == "wecom"
    assert "attacker-selected" not in command.content

    with pytest.raises(ValueError, match="tenant"):
        build_delivery_command(
            row,
            {**binding, "tenant_id": "tenant-b"},
            routing_key=key,
        )
    with pytest.raises(ValueError, match="ACTIVE"):
        build_delivery_command(
            row,
            {**binding, "status": "REVOKED"},
            routing_key=key,
        )


@pytest.mark.asyncio
async def test_uncertain_app_delivery_never_falls_back_to_bot(monkeypatch) -> None:
    """A response timeout may follow provider acceptance, so fallback would duplicate."""
    calls = {"bot": 0}

    async def timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout("response lost")

    class _Bot:
        async def send_msg(self, *_args, **_kwargs):
            calls["bot"] += 1
            return "bot-receipt"

    monkeypatch.setattr(wecom, "_send_app_message", timeout)
    monkeypatch.setattr("backend.integrations.wecom_bot.get_bot_client", lambda *_: _Bot())

    settings = Settings(
        _env_file=None,
        wecom_corp_secret="corp-secret",
        wecom_bot_id="bot-id",
        wecom_bot_secret="bot-secret",
    )
    with pytest.raises(wecom.DeliveryUnknownError):
        await wecom.send_message(settings, "user-1", "report ready")
    assert calls["bot"] == 0


@pytest.mark.asyncio
async def test_wecom_rejects_oversized_utf8_text_before_network(monkeypatch) -> None:
    called = False

    async def should_not_send(*_args, **_kwargs):
        nonlocal called
        called = True
        return "unexpected"

    monkeypatch.setattr(wecom, "_send_app_message", should_not_send)
    settings = Settings(_env_file=None)

    with pytest.raises(wecom.DeliveryRejectedError, match="2048"):
        await wecom.send_message(settings, "user-1", "中" * 683)
    assert called is False


class _JsonResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _SequenceClient:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def post(self, _url: str, *, params: dict, json: dict):
        del json
        self.tokens.append(params["access_token"])
        if len(self.tokens) == 1:
            return _JsonResponse({"errcode": 40014, "errmsg": "invalid access_token"})
        return _JsonResponse({"errcode": 0, "msgid": "accepted-1"})


@pytest.mark.asyncio
async def test_definitive_invalid_token_is_refreshed_and_retried_once(monkeypatch) -> None:
    issued = iter(["expired-token", "fresh-token"])
    client = _SequenceClient()

    async def token(_settings):
        return next(issued)

    async def http_client():
        return client

    monkeypatch.setattr(wecom, "get_access_token", token)
    monkeypatch.setattr(wecom, "get_http_client", http_client)

    result = await wecom._send_app_message(
        Settings(_env_file=None, wecom_agent_id="1000002"),
        "user-1",
        "report ready",
    )

    assert result == "accepted-1"
    assert client.tokens == ["expired-token", "fresh-token"]


def test_experimental_bot_registry_is_versioned_and_never_keys_by_secret() -> None:
    wecom_bot._clients.clear()
    try:
        first = wecom_bot.get_bot_client("bot-a", "secret-v1", "v1")
        same = wecom_bot.get_bot_client("bot-a", "ignored-secret", "v1")
        rotated = wecom_bot.get_bot_client("bot-a", "secret-v2", "v2")

        assert same is first
        assert rotated is not first
        serialized_keys = repr(tuple(wecom_bot._clients))
        assert "bot-a" not in serialized_keys
        assert "secret" not in serialized_keys
    finally:
        wecom_bot._clients.clear()


@pytest.mark.asyncio
async def test_experimental_bot_rejects_oversized_markdown_before_connect(
    monkeypatch,
) -> None:
    connected = False
    client = wecom_bot.WeComBotClient("bot-a", "secret")

    async def should_not_connect():
        nonlocal connected
        connected = True

    monkeypatch.setattr(client, "_ensure_connected", should_not_connect)

    with pytest.raises(ValueError, match="4096"):
        await client.send_msg("user-1", "中" * 1366)
    assert connected is False


class _DeliveryConnection:
    def __init__(self, bindings: list[dict] | None = None) -> None:
        self.bindings = bindings or []
        self.inserted: list[tuple] = []
        self.sql = ""

    async def fetch(self, sql: str, tenant_id: str):
        self.sql = sql
        assert tenant_id == "tenant-a"
        return self.bindings

    async def executemany(self, _sql: str, rows):
        self.inserted.extend(rows)


@pytest.mark.asyncio
async def test_report_publication_expands_one_outbox_per_active_member_binding() -> None:
    """A tenant report independently reaches every member with an ACTIVE binding."""
    conn = _DeliveryConnection(
        bindings=[
            {"id": "binding-1", "user_id": "user-1"},
            {"id": "binding-2", "user_id": "user-2"},
        ]
    )

    count = await insert_report_outbox_deliveries(
        conn,
        tenant_id="tenant-a",
        report_id="report-1",
        session="FUND_1430",
        version=2,
        trade_date="2026-08-26",
    )

    assert count == 2
    sql = " ".join(conn.sql.split()).upper()
    assert "USER_ID =" not in sql
    assert {row[1] for row in conn.inserted} == {"binding-1", "binding-2"}
    assert len({row[-1] for row in conn.inserted}) == 2
    assert all("touser" not in row[5] for row in conn.inserted)


@pytest.mark.asyncio
async def test_report_publication_without_active_binding_queues_nothing() -> None:
    """An unbound user never receives a push: no ACTIVE binding -> 0 rows."""
    conn = _DeliveryConnection(bindings=[])

    count = await insert_report_outbox_deliveries(
        conn,
        tenant_id="tenant-a",
        report_id="report-1",
        session="FUND_1430",
        version=2,
        trade_date="2026-08-26",
    )

    assert count == 0
    assert conn.inserted == []

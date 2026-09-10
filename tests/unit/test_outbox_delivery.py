"""Outbox and WeCom delivery regression tests (no network or database required)."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json

import httpx
import pytest

from backend.common.security import envelope_encrypt
from backend.config import Settings
from backend.integrations import wecom, wecom_bot
from backend.jobs.outbox import (
    DeliveryCommand,
    _send,
    _render_delivery_content,
    build_delivery_command,
    claim_batch,
)
from backend.reports.repository import (
    get_report_outbox_id,
    insert_report_outbox_deliveries,
)
from backend.reports.service import _delivery_audit_details


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
    monkeypatch.setattr(
        "backend.integrations.wecom_bot.get_bot_client", lambda *_: _Bot()
    )

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


@pytest.mark.asyncio
async def test_wecom_rejects_oversized_utf8_markdown_before_network(
    monkeypatch,
) -> None:
    called = False

    async def should_not_send(*_args, **_kwargs):
        nonlocal called
        called = True
        return "unexpected"

    monkeypatch.setattr(wecom, "_send_app_message", should_not_send)
    settings = Settings(_env_file=None)

    with pytest.raises(wecom.DeliveryRejectedError, match="2048"):
        await wecom.send_message(
            settings,
            "user-1",
            "中" * 683,
            msg_type="markdown",
        )
    assert called is False


@pytest.mark.asyncio
async def test_report_delivery_renders_a_clickable_internal_markdown_link(
    monkeypatch,
) -> None:
    key = "routing-key-with-at-least-32-bytes!"
    binding = {
        "id": "binding-1",
        "tenant_id": "tenant-a",
        "channel_type": "wecom",
        "status": "ACTIVE",
        "external_user_ciphertext": envelope_encrypt(key, "wecom-user-7"),
    }
    row = {
        "tenant_id": "tenant-a",
        "channel_binding_id": "binding-1",
        "topic": "REPORT_PUBLISHED",
        "payload": {
            "report_id": "report-1",
            "session": "POST_MARKET",
            "message_type": "markdown",
            "link_path": "/reports?reportId=report-1",
            "content": (
                "盘后报告 <font color=\"warning\">+1.23%</font> "
                "[外部链接](https://attacker.example) 已经生成。"
            ),
        },
    }
    command = build_delivery_command(row, binding, routing_key=key)
    captured: dict[str, str] = {}

    async def capture_send(_settings, recipient, content, *, msg_type="text"):
        captured.update(
            recipient=recipient,
            content=content,
            msg_type=msg_type,
        )
        return "message-1"

    monkeypatch.setattr(wecom, "send_message", capture_send)

    receipt = await _send(
        command,
        Settings(
            _env_file=None,
            wecom_corp_secret="configured",
            web_base_url="https://wealthsail.example.com",
        ),
    )

    assert receipt == "message-1"
    assert captured["recipient"] == "wecom-user-7"
    assert captured["msg_type"] == "markdown"
    assert "盘后报告" in captured["content"]
    assert '<font color="warning">+1.23%</font>' in captured["content"]
    assert "https://attacker.example" not in captured["content"]
    assert (
        "[查看完整报告](https://wealthsail.example.com/reports?reportId=report-1)"
        in captured["content"]
    )
    assert len(captured["content"].encode("utf-8")) <= 2048


def test_markdown_delivery_drops_an_incomplete_final_line_when_truncated() -> None:
    """A phone must not receive a cut-off holding row before the report link."""
    command = DeliveryCommand(
        channel_type="wecom",
        recipient="wecom-user-7",
        content="盘前持仓概览\n完整持仓行\n" + ("超长基金名称" * 500),
        message_type="markdown",
        link_path="/reports?reportId=report-1",
    )

    rendered = _render_delivery_content(
        command,
        Settings(
            _env_file=None,
            web_base_url="https://wealthsail.example.com",
        ),
    )

    assert "完整持仓行" in rendered
    assert "超长基金名称" not in rendered
    assert rendered.endswith(
        "[查看完整报告](https://wealthsail.example.com/reports?reportId=report-1)"
    )
    assert len(rendered.encode("utf-8")) <= 2048


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "web_base_url",
    [
        "https://user:password@wealthsail.example.com",
        "https://wealthsail.example.com/app",
        "https://wealthsail.example.com?next=external",
        "https://wealthsail.example.com#fragment",
    ],
)
async def test_report_delivery_requires_web_base_url_to_be_a_pure_origin(
    monkeypatch,
    web_base_url: str,
) -> None:
    called = False

    async def should_not_send(*_args, **_kwargs):
        nonlocal called
        called = True
        return "unexpected"

    monkeypatch.setattr(wecom, "send_message", should_not_send)
    command = DeliveryCommand(
        channel_type="wecom",
        recipient="wecom-user-7",
        content="报告已生成。",
        message_type="markdown",
        link_path="/reports?reportId=report-1",
    )

    with pytest.raises(wecom.DeliveryRejectedError, match="WEB_BASE_URL"):
        await _send(
            command,
            Settings(
                _env_file=None,
                wecom_corp_secret="configured",
                web_base_url=web_base_url,
            ),
        )
    assert called is False


def test_report_delivery_rejects_an_external_link_path() -> None:
    key = "routing-key-with-at-least-32-bytes!"
    binding = {
        "id": "binding-1",
        "tenant_id": "tenant-a",
        "channel_type": "wecom",
        "status": "ACTIVE",
        "external_user_ciphertext": envelope_encrypt(key, "wecom-user-7"),
    }
    row = {
        "tenant_id": "tenant-a",
        "channel_binding_id": "binding-1",
        "topic": "REPORT_PUBLISHED",
        "payload": {
            "report_id": "report-1",
            "session": "POST_MARKET",
            "message_type": "markdown",
            "link_path": "https://attacker.example/reports?reportId=report-1",
        },
    }

    with pytest.raises(ValueError, match="link path"):
        build_delivery_command(row, binding, routing_key=key)


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
async def test_definitive_invalid_token_is_refreshed_and_retried_once(
    monkeypatch,
) -> None:
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

    async def fetch(
        self, sql: str, tenant_id: str, session: str, target_user_id: str | None
    ):
        self.sql = sql
        assert tenant_id == "tenant-a"
        assert session in {"PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET"}
        if target_user_id is not None:
            return [
                binding
                for binding in self.bindings
                if binding["user_id"] == target_user_id
            ]
        return self.bindings

    async def executemany(self, _sql: str, rows):
        self.inserted.extend(rows)


@pytest.mark.asyncio
async def test_report_publication_expands_one_outbox_per_active_member_binding() -> (
    None
):
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
    assert "$3::UUID IS NULL OR BINDING.USER_ID = $3::UUID" in sql
    assert {row[1] for row in conn.inserted} == {"binding-1", "binding-2"}
    assert len({row[-1] for row in conn.inserted}) == 2
    assert all("touser" not in row[5] for row in conn.inserted)
    assert all("content" not in json.loads(row[5]) for row in conn.inserted)
    assert all(
        json.loads(row[5])["message_type"] == "markdown" for row in conn.inserted
    )
    assert all(
        json.loads(row[5])["link_path"] == "/reports?reportId=report-1"
        for row in conn.inserted
    )


@pytest.mark.asyncio
async def test_no_holdings_report_publication_includes_shared_brief_content() -> None:
    conn = _DeliveryConnection(bindings=[{"id": "binding-1", "user_id": "user-1"}])

    count = await insert_report_outbox_deliveries(
        conn,
        tenant_id="tenant-a",
        report_id="report-1",
        session="POST_MARKET",
        version=1,
        trade_date="2026-08-31",
        content="今日大盘风向：震荡。仅为市场信息，不构成投资建议。",
    )

    assert count == 1
    payload = json.loads(conn.inserted[0][5])
    assert payload["content"].startswith("今日大盘风向")
    assert payload["report_id"] == "report-1"
    assert payload["message_type"] == "markdown"
    assert payload["link_path"] == "/reports?reportId=report-1"


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


@pytest.mark.asyncio
async def test_split_pre_market_phases_get_distinct_outbox_idempotency_keys() -> None:
    """The 08:30 macro and 08:35 personalized reports must both deliver.

    Both phases persist report rows with the same (session, trade_date) and
    each starts its own version at 1, so the outbox idempotency key must
    include the report id — a session-level key collides and the
    ON CONFLICT DO NOTHING upsert silently drops the second push.
    """
    conn = _DeliveryConnection(bindings=[{"id": "binding-1", "user_id": "user-1"}])

    await insert_report_outbox_deliveries(
        conn,
        tenant_id="tenant-a",
        report_id="report-macro",
        session="PRE_MARKET",
        version=1,
        trade_date="2026-09-04",
    )
    await insert_report_outbox_deliveries(
        conn,
        tenant_id="tenant-a",
        report_id="report-personalized",
        session="PRE_MARKET",
        version=1,
        trade_date="2026-09-04",
    )

    assert len(conn.inserted) == 2
    macro_key = conn.inserted[0][-1]
    personalized_key = conn.inserted[1][-1]
    assert macro_key != personalized_key
    assert "report-macro" in macro_key
    assert "report-personalized" in personalized_key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session", "disabled_column"),
    [
        ("PRE_MARKET", "pre_market_report_enabled"),
        ("INTRADAY", "intraday_report_enabled"),
        ("FUND_1430", "fund_1430_report_enabled"),
        ("POST_MARKET", "post_market_report_enabled"),
    ],
)
async def test_delivery_query_filters_the_matching_disabled_session(
    session: str, disabled_column: str
) -> None:
    conn = _DeliveryConnection(
        bindings=[{"id": "binding-enabled", "user_id": "user-enabled"}]
    )

    count = await insert_report_outbox_deliveries(
        conn,
        tenant_id="tenant-a",
        report_id="report-1",
        session=session,
        version=1,
        trade_date="2026-09-01",
    )

    assert count == 1
    assert conn.inserted[0][1] == "binding-enabled"
    assert "LEFT JOIN identity.user_preferences" in conn.sql
    assert "COALESCE" in conn.sql
    assert "CASE $2" in conn.sql
    assert disabled_column in conn.sql


@pytest.mark.asyncio
async def test_delivery_can_target_one_user_without_changing_payload_contract() -> None:
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
        session="POST_MARKET",
        version=1,
        trade_date="2026-09-01",
        target_user_id="user-2",
    )

    assert count == 1
    assert conn.inserted[0][1] == "binding-2"
    assert "$3::uuid IS NULL OR binding.user_id = $3::uuid" in conn.sql
    assert "JOIN identity.tenant_members membership" in conn.sql
    assert "membership.status = 'ACTIVE'" in conn.sql


@pytest.mark.asyncio
async def test_report_outbox_lookup_is_tenant_report_and_user_scoped() -> None:
    class LookupConnection:
        def __init__(self) -> None:
            self.call: tuple[str, tuple] | None = None

        async def fetchval(self, sql: str, *args):
            self.call = (sql, args)
            return "outbox-1"

    conn = LookupConnection()

    result = await get_report_outbox_id(  # type: ignore[arg-type]
        conn, "tenant-a", "report-1", "user-2"
    )

    assert result == "outbox-1"
    assert conn.call is not None
    sql, args = conn.call
    assert "JOIN identity.channel_bindings" in sql
    assert "outbox.tenant_id = $1" in sql
    assert "outbox.aggregate_id = $2" in sql
    assert "binding.user_id = $3" in sql
    assert args == ("tenant-a", "report-1", "user-2")


def test_zero_delivery_is_explicit_in_report_audit_details() -> None:
    assert _delivery_audit_details(
        session="FUND_1430", version=2, degraded=False, delivery_count=0
    ) == {
        "session": "FUND_1430",
        "variant": "STANDARD",
        "audienceUserId": None,
        "version": 2,
        "degraded": False,
        "deliveryCount": 0,
        "deliveryStatus": "NO_ACTIVE_BINDING",
    }
    assert (
        _delivery_audit_details(
            session="PRE_MARKET", version=1, degraded=True, delivery_count=2
        )["deliveryStatus"]
        == "QUEUED"
    )

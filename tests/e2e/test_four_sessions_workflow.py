"""Adapter and in-memory domain-flow tests; not a production four-session E2E."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest

from adapters.messaging.wecom_adapter import WeComAdapter
from backend.jobs.scheduler import SESSION_LABELS, SESSION_ORDER
from backend.reports import repository as _reports
from modules.ledger.service import LedgerService


def test_wecom_token_verification_and_expiry() -> None:
    """Tests a high-entropy one-time token hash, verification, and expiration."""
    raw_token = "WS-8K4P-9Q2M"
    token_secret = "test-only-binding-secret"
    token_hash = WeComAdapter.hash_binding_token(raw_token, token_secret)

    # Valid unexpired token
    future_time = datetime.now(timezone.utc) + timedelta(minutes=15)
    assert (
        WeComAdapter.verify_binding_token(
            raw_token, token_hash, future_time, token_secret
        )
        is True
    )

    # Wrong token
    assert (
        WeComAdapter.verify_binding_token(
            "WS-0000-0000", token_hash, future_time, token_secret
        )
        is False
    )

    # Expired token
    past_time = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert (
        WeComAdapter.verify_binding_token(
            raw_token, token_hash, past_time, token_secret
        )
        is False
    )

    # Ambiguous naive timestamps fail closed instead of being compared in the
    # host machine's local timezone.
    naive_future = datetime.now() + timedelta(minutes=15)
    assert (
        WeComAdapter.verify_binding_token(
            raw_token, token_hash, naive_future, token_secret
        )
        is False
    )


def test_wecom_rejects_missing_or_broadcast_recipient() -> None:
    """A private financial message must never fall back to a broadcast target."""
    adapter = WeComAdapter(corp_id="corp", corp_secret="secret", agent_id=1)

    with pytest.raises(ValueError, match="收件人"):
        adapter.send_markdown_message(content="private", to_user="")
    with pytest.raises(ValueError, match="群发"):
        adapter.send_markdown_message(content="private", to_user="@all")


def test_wecom_missing_credentials_fail_closed_unless_dry_run() -> None:
    """Missing credentials are configuration errors, not successful delivery."""
    adapter = WeComAdapter(corp_id="", corp_secret="", agent_id=1)

    result = adapter.send_markdown_message(content="private", to_user="user-1")
    assert result["status"] == "CONFIG_ERROR"

    dry_run = adapter.send_markdown_message(
        content="private",
        to_user="user-1",
        idempotency_key="dry-run-1",
        dry_run=True,
    )
    assert dry_run["status"] == "DRY_RUN"


def test_wecom_transport_error_does_not_leak_credentials(monkeypatch, caplog) -> None:
    """Transport failures must not echo Corp Secret into results or logs."""
    secret = "never-print-this-corp-secret"
    adapter = WeComAdapter(corp_id="corp", corp_secret=secret, agent_id=1)

    def raise_transport_error(*_args, **_kwargs):
        raise RuntimeError(f"request failed: corpsecret={secret}")

    monkeypatch.setattr(
        "adapters.messaging.wecom_adapter.requests.get", raise_transport_error
    )

    result = adapter.send_markdown_message(content="private", to_user="user-1")
    visible_output = f"{result} {caplog.text}"
    assert result["status"] == "ERROR"
    assert secret not in visible_output


def test_wecom_idempotent_message_suppression() -> None:
    """The prototype suppresses duplicate dry-run messages within one process."""
    adapter = WeComAdapter()
    idem_key = "PRE_MARKET_REPORT_20260824_TENANT1"

    # First send
    res1 = adapter.send_markdown_message(
        content="### 🌅 盘前操盘剧本",
        to_user="tenant-1-user",
        idempotency_key=idem_key,
        dry_run=True,
    )
    assert res1["status"] == "DRY_RUN"

    # Second send with same key
    res2 = adapter.send_markdown_message(
        content="### 🌅 盘前操盘剧本 (重发)",
        to_user="tenant-1-user",
        idempotency_key=idem_key,
        dry_run=True,
    )
    assert res2["status"] == "DUPLICATE_IGNORED"


def test_confirmed_fill_updates_ledger_projection() -> None:
    """A separately confirmed fill produces the expected ledger projection."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    ledger = LedgerService(tenant_id=tenant_id, account_id=account_id)

    # Opening facts: user has 500,000 cash and a confirmed 300-share fill.
    ledger.record_cash_in(Decimal("500000.00"), date(2026, 8, 24), "DEP_E2E_01")
    ledger.record_buy(
        inst_id,
        Decimal("300.0000"),
        Decimal("1400.00"),
        Decimal("0.00"),
        date(2026, 8, 24),
        "BUY_E2E_01",
    )

    proj_pre = ledger.rebuild_projection()
    assert proj_pre.cash_balance == Decimal("80000.00")
    assert proj_pre.positions[inst_id].quantity == Decimal("300.0000")

    # Accepting a recommendation is deliberately outside LedgerService. Only a
    # separately confirmed fill reaches this deterministic ledger boundary.
    ledger.record_buy(
        inst_id,
        Decimal("50.0000"),
        Decimal("1380.00"),
        Decimal("0.00"),
        date(2026, 8, 24),
        "CONFIRMED_FILL_01",
    )
    proj_intra = ledger.rebuild_projection()
    # Cash: 80,000 - 69,000 = 11,000.00
    assert proj_intra.cash_balance == Decimal("11000.00")
    # Shares: 350.0000, Total Cost: 420,000 + 69,000 = 489,000 -> Avg Cost = 489,000 / 350 = 1397.1429
    assert proj_intra.positions[inst_id].quantity == Decimal("350.0000")
    assert proj_intra.positions[inst_id].avg_cost == Decimal("1397.1429")

    # Rebuilding the projection is the prototype reconciliation check. Formal
    # four-session scheduling/reporting/Outbox E2E remains NOT_RUN.
    proj_post = ledger.rebuild_projection()
    assert proj_post.cash_balance == Decimal("11000.00")
    assert proj_post.positions[inst_id].quantity == Decimal("350.0000")


# ---------------------------------------------------------------------------
# Four-session + split PRE_MARKET report workflow contract
#
# The public session enum stays at exactly four. PRE_MARKET expands internally
# into a tenant-wide macro report (audience_user_id IS NULL) and a per-user
# personalized report (audience_user_id = the member). The repository's
# visibility predicate keeps one member from reading another member's
# personalized report; a missing personalized row is invisible (API -> 404).
# ---------------------------------------------------------------------------


def test_public_session_set_remains_four_and_unchanged() -> None:
    """Migration 045 / the scheduler split must not add a fifth public session."""
    assert SESSION_ORDER == ("PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET")
    assert set(SESSION_LABELS) == {
        "PRE_MARKET",
        "INTRADAY",
        "FUND_1430",
        "POST_MARKET",
    }


class _Rows(list):
    """Minimal asyncpg.fetch stand-in returning a fixed row list."""


class _Conn:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def fetch(self, _sql: str, *_args) -> list[dict]:
        return self._rows

    async def fetchrow(self, _sql: str, *_args):
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_pre_market_list_shows_macro_and_current_user_personalized() -> None:
    """A member with holdings/watchlist sees the macro report plus their own."""
    rows = _Rows(
        [
            {
                "id": "macro",
                "report_variant": "PRE_MARKET_MACRO",
                "audience_user_id": None,
            },
            {
                "id": "me-personal",
                "report_variant": "PRE_MARKET_PERSONALIZED",
                "audience_user_id": "user-1",
            },
        ]
    )
    result = await _reports.list_reports(
        _Conn(rows),
        "tenant-1",
        date(2026, 9, 2),
        "PRE_MARKET",
        actor_user_id="user-1",
    )
    identities = {(r["report_variant"], r["audience_user_id"]) for r in result}
    assert ("PRE_MARKET_MACRO", None) in identities
    assert ("PRE_MARKET_PERSONALIZED", "user-1") in identities
    # Another member's personalized report is never returned to user-1.
    assert ("PRE_MARKET_PERSONALIZED", "user-2") not in identities


@pytest.mark.asyncio
async def test_pre_market_list_without_context_shows_only_macro() -> None:
    """No holdings and no watchlist -> only the tenant-wide macro report."""
    rows = _Rows(
        [
            {
                "id": "macro",
                "report_variant": "PRE_MARKET_MACRO",
                "audience_user_id": None,
            },
        ]
    )
    result = await _reports.list_reports(
        _Conn(rows),
        "tenant-1",
        date(2026, 9, 2),
        "PRE_MARKET",
        actor_user_id="user-1",
    )
    assert len(result) == 1
    assert result[0]["report_variant"] == "PRE_MARKET_MACRO"
    assert result[0]["audience_user_id"] is None


@pytest.mark.asyncio
async def test_personalized_report_detail_resolves_only_for_audience() -> None:
    """Detail resolves both IDs for the owner; a non-owner gets None (404)."""
    personalized = {
        "id": "me-personal",
        "report_variant": "PRE_MARKET_PERSONALIZED",
        "audience_user_id": "user-1",
    }
    owned = await _reports.get_report(
        _Conn([personalized]), "tenant-1", "me-personal", actor_user_id="user-1"
    )
    assert owned is not None
    assert owned["audience_user_id"] == "user-1"

    other = await _reports.get_report(
        _Conn([]), "tenant-1", "me-personal", actor_user_id="user-2"
    )
    assert other is None

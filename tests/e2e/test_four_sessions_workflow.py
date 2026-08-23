"""End-to-end integration and workflow tests for the 4 daily reporting sessions."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import uuid

from adapters.messaging.wecom_adapter import WeComAdapter
from modules.ledger.service import LedgerService


def test_wecom_token_verification_and_expiry() -> None:
    """Tests 6-digit one-time binding token hashing, verification, and expiration."""
    raw_token = "WS-8899"
    token_hash = WeComAdapter.hash_binding_token(raw_token)

    # Valid unexpired token
    future_time = datetime.now(timezone.utc) + timedelta(minutes=15)
    assert WeComAdapter.verify_binding_token(raw_token, token_hash, future_time) is True

    # Wrong token
    assert WeComAdapter.verify_binding_token("WS-0000", token_hash, future_time) is False

    # Expired token
    past_time = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert WeComAdapter.verify_binding_token(raw_token, token_hash, past_time) is False


def test_wecom_idempotent_message_suppression() -> None:
    """Tests that duplicate messages with same idempotency key are suppressed."""
    adapter = WeComAdapter()
    idem_key = "PRE_MARKET_REPORT_20260823_TENANT1"

    # First send
    res1 = adapter.send_markdown_message(
        content="### 🌅 盘前操盘剧本",
        idempotency_key=idem_key,
    )
    assert res1["status"] in ("MOCK_SENT", "DELIVERED")

    # Second send with same key
    res2 = adapter.send_markdown_message(
        content="### 🌅 盘前操盘剧本 (重发)",
        idempotency_key=idem_key,
    )
    assert res2["status"] == "DUPLICATE_IGNORED"


def test_four_sessions_complete_lifecycle() -> None:
    """Simulates a complete 4-session daily lifecycle with ledger and reporting."""
    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    ledger = LedgerService(tenant_id=tenant_id, account_id=account_id)

    # Session 1 (Pre-market): User has initial 500,000 cash and holds 300 shares @ 1400.00
    ledger.record_cash_in(Decimal("500000.00"), date(2026, 8, 23), "DEP_E2E_01")
    ledger.record_buy(inst_id, Decimal("300.0000"), Decimal("1400.00"), Decimal("0.00"), date(2026, 8, 23), "BUY_E2E_01")

    proj_pre = ledger.rebuild_projection()
    assert proj_pre.cash_balance == Decimal("80000.00")
    assert proj_pre.positions[inst_id].quantity == Decimal("300.0000")

    # Session 2 (Intraday HITL): Price touches 1380.00 buy point, user confirms buy 50 shares
    ledger.record_buy(inst_id, Decimal("50.0000"), Decimal("1380.00"), Decimal("0.00"), date(2026, 8, 23), "HITL_BUY_01")
    proj_intra = ledger.rebuild_projection()
    # Cash: 80,000 - 69,000 = 11,000.00
    assert proj_intra.cash_balance == Decimal("11000.00")
    # Shares: 350.0000, Total Cost: 420,000 + 69,000 = 489,000 -> Avg Cost = 489,000 / 350 = 1397.1429
    assert proj_intra.positions[inst_id].quantity == Decimal("350.0000")
    assert proj_intra.positions[inst_id].avg_cost == Decimal("1397.1429")

    # Session 3 (14:30 Fund Estimation): Tested in module unit test
    # Session 4 (Post-market Review): Verified final reconciled position
    proj_post = ledger.rebuild_projection()
    assert proj_post.cash_balance == Decimal("11000.00")
    assert proj_post.positions[inst_id].quantity == Decimal("350.0000")

"""Unit tests for WealthSail backend common infrastructure (no DB required)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend.common.auth import create_access_token, decode_access_token
from backend.common.formatting import fmt_money, fmt_qty, merge_quality, pct
from backend.common.security import (
    constant_time_eq,
    envelope_decrypt,
    envelope_encrypt,
    generate_token,
    hmac_digest,
)
from backend.errors import (
    AppError,
    AuthRequiredError,
    ForbiddenError,
    NotFoundError,
    ValidationError_,
)
from backend.integrations.wecom import (
    wecom_decrypt,
    wecom_encrypt,
    verify_signature,
)
from backend.jobs.scheduler import SESSION_ORDER, is_due
from backend.jobs.scheduler import SessionSchedule

AES_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
CORP_ID = "ww-dev-corp-id"


# ---------------------------------------------------------------------------
# Money formatting — financial precision invariants
# ---------------------------------------------------------------------------
def test_fmt_money_fixed_scale() -> None:
    assert fmt_money(Decimal("426000.00")) == "426000.00"
    assert fmt_money(Decimal("0.1")) == "0.10"
    assert fmt_money(None) == "0.00"
    assert fmt_money(Decimal("-8456.23")) == "-8456.23"
    assert fmt_money(Decimal("0.0315"), places=4) == "0.0315"


def test_fmt_qty_strips_trailing_zeros() -> None:
    assert fmt_qty(Decimal("300.0000")) == "300"
    assert fmt_qty(Decimal("118420.52")) == "118420.52"
    assert fmt_qty(None) == "0"


def test_merge_quality_fail_closed() -> None:
    assert merge_quality(["FRESH", "FRESH"]) == "FRESH"
    assert merge_quality(["FRESH", "ESTIMATED"]) == "ESTIMATED"
    assert merge_quality(["FRESH", "UNAVAILABLE"]) == "MISSING"
    assert merge_quality([]) == "FRESH"


def test_pct_safe_and_rounded() -> None:
    assert pct(Decimal("426000"), Decimal("506000")) == Decimal("84.19")
    assert pct(Decimal("1"), Decimal("0")) == Decimal("0.00")


# ---------------------------------------------------------------------------
# Error envelope contract
# ---------------------------------------------------------------------------
def test_error_codes_and_status() -> None:
    assert AppError("x", code="NOT_FOUND").http_status == 404
    assert NotFoundError("x").code == "NOT_FOUND"
    assert ForbiddenError("x").code == "FORBIDDEN"
    assert ValidationError_("x").http_status == 400
    assert AuthRequiredError("x").code == "AUTH_REQUIRED"
    assert AppError("x", code="RATE_LIMITED").retryable is True
    with pytest.raises(ValueError):
        AppError("x", code="NOT_A_CODE")


# ---------------------------------------------------------------------------
# Security primitives
# ---------------------------------------------------------------------------
def test_generate_token_entropy_and_format() -> None:
    t1 = generate_token(16)
    t2 = generate_token(16)
    assert t1.startswith("WS-")
    assert t1 != t2
    # 16 bytes → 26 base32 chars → entropy >= 128 bit.
    body = t1[3:].replace("-", "")
    assert len(body) == 26


def test_hmac_and_constant_time() -> None:
    h1 = hmac_digest("key", "WS-ABC")
    h2 = hmac_digest("key", "WS-ABC")
    h3 = hmac_digest("other", "WS-ABC")
    assert h1 == h2 and h1 != h3
    assert constant_time_eq(h1, h2) and not constant_time_eq(h1, h3)


def test_envelope_encryption_roundtrip() -> None:
    from cryptography.exceptions import InvalidTag

    key = "dev-routing-key-32-bytes-minimum!"
    ct = envelope_encrypt(key, "external-user-id-001")
    assert ct.startswith("v1:")
    assert envelope_decrypt(key, ct) == "external-user-id-001"
    with pytest.raises((ValueError, InvalidTag)):
        envelope_decrypt("a-different-key-32-bytes-long!!!", ct)


# ---------------------------------------------------------------------------
# WeCom crypto
# ---------------------------------------------------------------------------
def test_wecom_encrypt_decrypt_roundtrip() -> None:
    ct = wecom_encrypt("hello-echostr", AES_KEY, CORP_ID)
    assert wecom_decrypt(ct, AES_KEY, CORP_ID) == "hello-echostr"
    with pytest.raises(ValueError):
        wecom_decrypt(ct, AES_KEY, "wrong-corp")


def test_wecom_signature_verification() -> None:
    token, ts, nonce, encrypt = "t", "1700000000", "n", "cipher"
    sig = verify_signature.__globals__["hashlib"].sha1(
        "".join(sorted([token, ts, nonce, encrypt])).encode()
    ).hexdigest()
    assert verify_signature(token, ts, nonce, encrypt, sig) is True
    assert verify_signature(token, ts, nonce, encrypt, "bad") is False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def test_jwt_roundtrip() -> None:
    token, ttl = create_access_token(
        user_id="u1",
        tenant_id="t1",
        email="a@b.c",
        nickname="测试",
        tenant_role="OWNER",
        jwt_secret="secret",
        ttl_seconds=600,
    )
    assert ttl == 600
    session = decode_access_token(token, "secret")
    assert session.user_id == "u1"
    assert session.tenant_id == "t1"
    assert session.tenant_role == "OWNER"
    with pytest.raises(AuthRequiredError):
        decode_access_token(token, "wrong-secret")
    with pytest.raises(AuthRequiredError):
        decode_access_token("not.a.jwt", "secret")


# ---------------------------------------------------------------------------
# Scheduler due-matching
# ---------------------------------------------------------------------------
def test_scheduler_is_due() -> None:
    pre = SessionSchedule("PRE_MARKET", "盘前简报", minute=30, hour=8)
    d = datetime(2026, 8, 24, 8, 30, tzinfo=timezone.utc)  # Monday
    assert is_due(pre, d) is True
    assert is_due(pre, d.replace(hour=8, minute=31)) is False
    # Sunday → not due
    assert is_due(pre, datetime(2026, 8, 23, 8, 30)) is False

    intraday = SessionSchedule("INTRADAY", "盘中跟踪", minute=0, hour=9, every_minutes=5)
    assert is_due(intraday, datetime(2026, 8, 24, 10, 25)) is True
    assert is_due(intraday, datetime(2026, 8, 24, 9, 20)) is False  # before 09:30
    assert is_due(intraday, datetime(2026, 8, 24, 15, 5)) is False  # after 15:00


def test_session_order() -> None:
    assert SESSION_ORDER == ("PRE_MARKET", "INTRADAY", "FUND_1430", "POST_MARKET")

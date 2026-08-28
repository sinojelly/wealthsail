"""Persistent replay protection for authenticated WeCom callbacks."""

from __future__ import annotations

import base64
import hashlib
import struct
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from starlette.requests import Request

from backend.config import Settings
from backend.integrations import wecom


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "026_wecom_callback_replay_guard.sql"


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _ReplayConnection:
    def __init__(self) -> None:
        self.claimed: set[str] = set()
        self.claim_calls: list[str] = []
        self.statements: list[str] = []

    def transaction(self) -> _Transaction:
        return _Transaction()

    async def execute(self, sql: str, *_args):
        self.statements.append(sql)
        return "SELECT 1"

    async def fetchval(self, sql: str, fingerprint: str) -> bool:
        self.statements.append(sql)
        self.claim_calls.append(fingerprint)
        if fingerprint in self.claimed:
            return False
        self.claimed.add(fingerprint)
        return True


class _Acquire:
    def __init__(self, conn: _ReplayConnection) -> None:
        self.conn = conn

    async def __aenter__(self) -> _ReplayConnection:
        return self.conn

    async def __aexit__(self, *_args):
        return None


class _ReplayPool:
    def __init__(self) -> None:
        self.conn = _ReplayConnection()

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


class _Identity:
    def __init__(self) -> None:
        self.consume_count = 0

    async def consume_binding_challenge(self, **_kwargs):
        self.consume_count += 1
        return {
            "bindingId": "binding-1",
            "tenantId": "tenant-1",
            "status": "ACTIVE",
        }


def _request(body: bytes) -> Request:
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/adapters/wecom/callback",
            "headers": [],
            "query_string": b"",
        },
        receive,
    )


def _callback_fixture(
    settings: Settings,
    *,
    msg_id: str | None,
    random_prefix: bytes | None = None,
) -> tuple[bytes, str, str, str]:
    timestamp = str(int(time.time()))
    nonce = "fixed-callback-nonce"
    msg_id_xml = f"<MsgId>{msg_id}</MsgId>" if msg_id is not None else ""
    inner = (
        "<xml>"
        "<FromUserName><![CDATA[wecom-user-7]]></FromUserName>"
        "<MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[/bind WS-TEST-TOKEN]]></Content>"
        f"{msg_id_xml}"
        "</xml>"
    )
    if random_prefix is None:
        encrypted = wecom.wecom_encrypt(
            inner, settings.wecom_callback_aes_key, settings.wecom_corp_id
        )
    else:
        assert len(random_prefix) == 16
        key_text = settings.wecom_callback_aes_key
        key = base64.b64decode(key_text + "=" * ((4 - len(key_text) % 4) % 4))
        plaintext = inner.encode("utf-8")
        raw = (
            random_prefix
            + struct.pack(">I", len(plaintext))
            + plaintext
            + settings.wecom_corp_id.encode("utf-8")
        )
        pad_len = 32 - (len(raw) % 32)
        padded = raw + bytes([pad_len]) * pad_len
        encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
        encrypted = base64.b64encode(
            encryptor.update(padded) + encryptor.finalize()
        ).decode("ascii")
    signature = hashlib.sha1(
        "".join(
            sorted([settings.wecom_callback_token, timestamp, nonce, encrypted])
        ).encode("utf-8")
    ).hexdigest()
    envelope = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()
    return envelope, signature, timestamp, nonce


@pytest.mark.asyncio
@pytest.mark.parametrize("msg_id", ["938475610293847561", None])
async def test_authenticated_callback_replay_enters_domain_only_once(
    monkeypatch, msg_id: str | None
) -> None:
    settings = Settings(
        _env_file=None,
        wecom_callback_token="callback-token-for-tests",
        wecom_callback_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        wecom_corp_id="ww-test-corp",
    )
    pool = _ReplayPool()
    identity = _Identity()
    monkeypatch.setattr(wecom, "identity_service", lambda *_args: identity)
    body, signature, timestamp, nonce = _callback_fixture(settings, msg_id=msg_id)

    first = await wecom.wecom_callback(
        _request(body),
        msg_signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        pool=pool,
        settings=settings,
    )
    replay = await wecom.wecom_callback(
        _request(body),
        msg_signature=signature,
        timestamp=timestamp,
        nonce=nonce,
        pool=pool,
        settings=settings,
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert identity.consume_count == 1
    assert len(pool.conn.claim_calls) == 2
    assert pool.conn.claim_calls[0] == pool.conn.claim_calls[1]
    assert pool.conn.claim_calls[0].startswith("sha256:")
    assert any("SET LOCAL ROLE wealthsail_api" in sql for sql in pool.conn.statements)


@pytest.mark.asyncio
async def test_callback_without_msgid_deduplicates_reencrypted_same_payload(
    monkeypatch,
) -> None:
    settings = Settings(
        _env_file=None,
        wecom_callback_token="callback-token-for-tests",
        wecom_callback_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
        wecom_corp_id="ww-test-corp",
    )
    pool = _ReplayPool()
    identity = _Identity()
    monkeypatch.setattr(wecom, "identity_service", lambda *_args: identity)
    first = _callback_fixture(settings, msg_id=None, random_prefix=b"A" * 16)
    reencrypted = _callback_fixture(settings, msg_id=None, random_prefix=b"B" * 16)

    for body, signature, timestamp, nonce in (first, reencrypted):
        response = await wecom.wecom_callback(
            _request(body),
            msg_signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            pool=pool,
            settings=settings,
        )
        assert response.status_code == 200

    assert identity.consume_count == 1
    assert pool.conn.claim_calls[0] == pool.conn.claim_calls[1]


def test_replay_guard_migration_exposes_only_atomic_claim_function() -> None:
    assert MIGRATION.exists()
    sql = MIGRATION.read_text(encoding="utf-8").upper()

    assert "CREATE TABLE WORKFLOW.WECOM_CALLBACK_CLAIMS" in sql
    assert "CREATE FUNCTION WORKFLOW.CLAIM_WECOM_CALLBACK" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET SEARCH_PATH = WORKFLOW, PG_CATALOG" in sql
    assert "ON CONFLICT (EVENT_FINGERPRINT) DO NOTHING" in sql
    assert "REVOKE ALL ON WORKFLOW.WECOM_CALLBACK_CLAIMS" in sql
    assert "FROM PUBLIC, AUTHENTICATED, WEALTHSAIL_API" in sql
    assert "GRANT EXECUTE ON FUNCTION WORKFLOW.CLAIM_WECOM_CALLBACK(TEXT)" in sql
    assert "TO WEALTHSAIL_API" in sql

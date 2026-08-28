"""WeCom callback adapter (Ingress).

Implements docs/10 §3.2 (URL verification GET) and §3.3 (encrypted POST with
one-time binding challenge consumption):

  * GET  /api/v1/adapters/wecom/callback — SHA1 signature check, replay window
    ≤ 300s, AES decrypt of echostr, CorpID validation, plaintext echo.
  * POST /api/v1/adapters/wecom/callback — signature + timestamp check, AES
    decrypt of the XML payload, consume ``/bind <token>`` for the external
    user id, encrypted passive reply (200 always, so WeCom never loops on
    protocol-level errors).

AES follows the WeCom WXBizMsgCrypt scheme: key = base64(aes_key + '='),
IV = key[:16], plaintext = random16 + msg_len(BE) + msg + receiveid,
PKCS7 padded, AES-256-CBC.
"""

from __future__ import annotations

import base64
import asyncio
import hashlib
import logging
import struct
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, Response

from backend.common.database import Pool, api_system_transaction
from backend.common.security import constant_time_eq
from backend.config import Settings
from backend.dependencies import get_pool, get_settings_dep
from backend.domain import identity_service

logger = logging.getLogger("wealthsail.wecom")

router = APIRouter(prefix="/adapters/wecom", tags=["wecom"])

REPLAY_WINDOW_SECONDS = 300
MAX_WECOM_TEXT_BYTES = 2048
INVALID_ACCESS_TOKEN_ERRCODES = frozenset({40001, 40014, 42001})

WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

# (tenant credential identity/version) -> token. Secrets are never cache keys.
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}
_token_locks: dict[tuple[str, str], asyncio.Lock] = {}
_shared_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()


def _credential_key(settings: Settings) -> tuple[str, str]:
    return settings.wecom_corp_id, settings.wecom_credential_version


async def get_http_client() -> httpx.AsyncClient:
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        async with _http_client_lock:
            if _shared_http_client is None or _shared_http_client.is_closed:
                _shared_http_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(15.0, connect=5.0),
                    limits=httpx.Limits(
                        max_connections=20,
                        max_keepalive_connections=10,
                    ),
                )
    return _shared_http_client


async def close_http_client() -> None:
    global _shared_http_client
    async with _http_client_lock:
        if _shared_http_client is not None:
            await _shared_http_client.aclose()
        _shared_http_client = None


class DeliveryUnknownError(RuntimeError):
    """The request may have reached WeCom; automatic retry can duplicate it."""


class DeliveryRejectedError(RuntimeError):
    """WeCom definitively rejected the request before accepting the message."""


# ---------------------------------------------------------------------------
# 主动推送（outbox 消费者 / 运营接口调用）
# ---------------------------------------------------------------------------
async def get_access_token(settings: Settings) -> str:
    """获取企业微信 access_token（进程内缓存，提前 300s 刷新）。

    Raises RuntimeError: 凭据无效或接口异常。
    """
    key = _credential_key(settings)
    cached = _token_cache.get(key)
    if cached and cached[1] > time.time() + 300:
        return cached[0]
    lock = _token_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _token_cache.get(key)
        if cached and cached[1] > time.time() + 300:
            return cached[0]
        client = await get_http_client()
        resp = await client.get(
            f"{WECOM_API_BASE}/gettoken",
            params={
                "corpid": settings.wecom_corp_id,
                "corpsecret": settings.wecom_corp_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"wecom gettoken failed: errcode={data.get('errcode')}")
        token = str(data["access_token"])
        expires_in = int(data.get("expires_in", 7200))
        _token_cache[key] = (token, time.time() + expires_in)
    logger.info("wecom access_token acquired (expires_in=%ss)", expires_in)
    return token


async def send_message(
    settings: Settings,
    user_id: str,
    content: str,
    *,
    msg_type: str = "text",
) -> str:
    """推送消息到企微成员。

    MVP 使用自建应用（gettoken + message/send，agent_id 发送）。Bot 是
    独立、默认关闭的实验通道，不能在超时等不确定结果后自动回退，
    否则同一报告可能被两个通道重复发送。

    Args:
        user_id: 企微通讯录成员 userid（touser / 单聊 chatid）。
        content: 消息文本（UTF-8，企微限制 2048 字节）。
        msg_type: 自建应用消息类型（默认 text）。

    Returns:
        自建应用返回的 msgid。

    Raises:
        DeliveryRejectedError: 参数或 Provider 明确拒绝。
        DeliveryUnknownError: 超时/网络故障导致投递结果未知，禁止自动换通道重发。
    """
    if not user_id.strip():
        raise DeliveryRejectedError("WeCom recipient must not be empty")
    if msg_type == "text" and len(content.encode("utf-8")) > MAX_WECOM_TEXT_BYTES:
        raise DeliveryRejectedError(
            f"WeCom text exceeds the {MAX_WECOM_TEXT_BYTES}-byte UTF-8 limit"
        )
    try:
        return await _send_app_message(settings, user_id, content, msg_type=msg_type)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise DeliveryUnknownError("WeCom delivery result is unknown") from exc
    except DeliveryRejectedError:
        raise
    except httpx.HTTPStatusError as exc:
        if 400 <= exc.response.status_code < 500:
            raise DeliveryRejectedError(
                f"WeCom HTTP rejection: {exc.response.status_code}"
            ) from exc
        raise DeliveryUnknownError(
            f"WeCom HTTP result is unknown: {exc.response.status_code}"
        ) from exc


async def _send_app_message(
    settings: Settings,
    user_id: str,
    content: str,
    *,
    msg_type: str = "text",
) -> str:
    """自建应用（message/send）推送：gettoken + 发送。"""
    body: dict[str, Any] = {
        "touser": user_id,
        "msgtype": msg_type,
        "agentid": int(settings.wecom_agent_id),
    }
    if msg_type == "text":
        body["text"] = {"content": content}
    else:
        body[msg_type] = {"content": content}
    client = await get_http_client()
    for attempt in range(2):
        token = await get_access_token(settings)
        resp = await client.post(
            f"{WECOM_API_BASE}/message/send",
            params={"access_token": token},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        errcode = int(data.get("errcode", -1))
        if errcode == 0:
            msgid = str(data.get("msgid", ""))
            recipient_ref = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]
            logger.info(
                "wecom app message accepted recipient_ref=%s (msgid=%s)",
                recipient_ref,
                msgid,
            )
            return msgid
        if errcode in INVALID_ACCESS_TOKEN_ERRCODES and attempt == 0:
            # The provider definitively rejected this request before accepting a
            # message, so refreshing the token and retrying exactly once is safe.
            _token_cache.pop(_credential_key(settings), None)
            continue
        raise DeliveryRejectedError(
            f"wecom message/send failed: errcode={errcode}"
        )
    raise DeliveryRejectedError("wecom message/send exhausted token refresh")


# ---------------------------------------------------------------------------
# WeCom AES / signature primitives
# ---------------------------------------------------------------------------
def _aes_key_bytes(aes_key: str) -> bytes:
    padded = aes_key + "=" * ((4 - len(aes_key) % 4) % 4)
    return base64.b64decode(padded)


def _pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len]) * pad_len


def _pkcs7_unpad(data: bytes) -> bytes:
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 32:
        raise ValueError("invalid pkcs7 padding")
    return data[:-pad_len]


def wecom_encrypt(plaintext: str, aes_key: str, receive_id: str) -> str:
    key = _aes_key_bytes(aes_key)
    iv = key[:16]
    raw = (
        b"\x00" * 16
        + struct.pack(">I", len(plaintext.encode("utf-8")))
        + plaintext.encode("utf-8")
        + receive_id.encode("utf-8")
    )
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(_pkcs7_pad(raw)) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


def wecom_decrypt(ciphertext_b64: str, aes_key: str, receive_id: str) -> str:
    key = _aes_key_bytes(aes_key)
    iv = key[:16]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    raw = _pkcs7_unpad(
        decryptor.update(base64.b64decode(ciphertext_b64)) + decryptor.finalize()
    )
    msg_len = struct.unpack(">I", raw[16:20])[0]
    msg = raw[20 : 20 + msg_len].decode("utf-8")
    actual_receive_id = raw[20 + msg_len :].decode("utf-8")
    if actual_receive_id != receive_id:
        raise ValueError("receiveid mismatch")
    return msg


def verify_signature(token: str, timestamp: str, nonce: str, encrypt: str, signature: str) -> bool:
    sort_list = sorted([token, timestamp, nonce, encrypt])
    digest = hashlib.sha1("".join(sort_list).encode("utf-8")).hexdigest()
    return constant_time_eq(digest, signature or "")


def _check_timestamp(timestamp: str) -> None:
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if abs(time.time() - ts) > REPLAY_WINDOW_SECONDS:
        raise ValueError("timestamp out of replay window")


def _build_encrypted_reply(
    content: str, *, settings: Settings, timestamp: str, nonce: str
) -> str:
    encrypt = wecom_encrypt(content, settings.wecom_callback_aes_key, settings.wecom_corp_id)
    signature = hashlib.sha1(
        "".join(sorted([settings.wecom_callback_token, timestamp, nonce, encrypt])).encode()
    ).hexdigest()
    return (
        "<xml>"
        f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
        f"<MsgSignature><![CDATA[{signature}]]></MsgSignature>"
        f"<TimeStamp>{timestamp}</TimeStamp>"
        f"<Nonce><![CDATA[{nonce}]]></Nonce>"
        "</xml>"
    )


def _parse_xml(body: str) -> dict[str, str]:
    root = ET.fromstring(body)
    return {child.tag: (child.text or "") for child in root}


def _callback_event_fingerprint(
    corp_id: str, inner: dict[str, str], authenticated_payload: str
) -> str:
    """Opaque replay key scoped to one CorpID without persisting external IDs."""
    msg_id = inner.get("MsgId", "").strip()
    if msg_id:
        event_identity = f"msgid:{msg_id}"
    else:
        payload_hash = hashlib.sha256(
            authenticated_payload.encode("utf-8")
        ).hexdigest()
        event_identity = f"payload-sha256:{payload_hash}"
    material = f"wecom\x00{corp_id}\x00{event_identity}".encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


async def _claim_callback_once(pool: Pool, event_fingerprint: str) -> bool:
    """Persist the replay claim under the restricted API role."""
    async with api_system_transaction(pool) as conn:
        claimed = await conn.fetchval(
            "SELECT workflow.claim_wecom_callback($1)", event_fingerprint
        )
    return claimed is True


# ---------------------------------------------------------------------------
# Callback endpoints
# ---------------------------------------------------------------------------
@router.get("/callback")
async def wecom_url_verify(
    msg_signature: str = Query(default=""),
    timestamp: str = Query(default=""),
    nonce: str = Query(default=""),
    echostr: str = Query(default=""),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """企业微信后台的 URL 验证：返回解密后的 echostr 纯文本。"""
    try:
        _check_timestamp(timestamp)
        if not verify_signature(
            settings.wecom_callback_token, timestamp, nonce, echostr, msg_signature
        ):
            return PlainTextResponse("signature mismatch", status_code=403)
        plaintext = wecom_decrypt(echostr, settings.wecom_callback_aes_key, settings.wecom_corp_id)


    except (ValueError, ET.ParseError) as exc:
        logger.warning("wecom URL verify failed: %s", exc)
        return PlainTextResponse("verify failed", status_code=403)
    return PlainTextResponse(plaintext)


@router.post("/callback")
async def wecom_callback(
    request: Request,
    msg_signature: str = Query(default=""),
    timestamp: str = Query(default=""),
    nonce: str = Query(default=""),
    pool: Pool = Depends(get_pool),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """加密消息回调：验签 → 解密 → 消费 /bind 绑定口令 → 加密被动回复。"""
    body_bytes = await request.body()
    if len(body_bytes) > 64 * 1024:
        return PlainTextResponse("payload too large", status_code=413)
    body = body_bytes.decode("utf-8", errors="replace")

    try:
        envelope = _parse_xml(body)
        encrypt = envelope.get("Encrypt", "")
        _check_timestamp(timestamp)
        if not verify_signature(
            settings.wecom_callback_token, timestamp, nonce, encrypt, msg_signature
        ):
            raise ValueError("signature mismatch")
        authenticated_payload = wecom_decrypt(
            encrypt, settings.wecom_callback_aes_key, settings.wecom_corp_id
        )
        inner = _parse_xml(authenticated_payload)
    except (ValueError, ET.ParseError) as exc:
        logger.warning("wecom callback rejected: %s", exc)
        reply = _build_encrypted_reply(
            "回调验证失败", settings=settings, timestamp=timestamp, nonce=nonce
        )
        return Response(reply, media_type="application/xml")

    event_fingerprint = _callback_event_fingerprint(
        settings.wecom_corp_id, inner, authenticated_payload
    )
    try:
        claimed = await _claim_callback_once(pool, event_fingerprint)
    except Exception:  # noqa: BLE001 -- replay storage failure must fail closed
        logger.exception("wecom callback replay claim failed")
        return PlainTextResponse("temporarily unavailable", status_code=503)
    if not claimed:
        logger.info("wecom callback replay suppressed")
        reply = _build_encrypted_reply(
            "收到", settings=settings, timestamp=timestamp, nonce=nonce
        )
        return Response(reply, media_type="application/xml")

    from_user = inner.get("FromUserName", "")
    content = inner.get("Content", "").strip()
    msg_type = inner.get("MsgType", "")

    reply_content = "收到"
    if msg_type == "text" and content.lower().startswith("/bind"):
        token = content[5:].strip()
        try:
            result = await identity_service(pool, settings).consume_binding_challenge(
                token=token,
                external_user_id=from_user,
                external_username=inner.get("Alias") or None,
            )
            logger.info(
                "wecom binding consumed: binding=%s tenant=%s",
                result["bindingId"],
                result.get("tenantId", "?"),
            )
            reply_content = "✅ 身份绑定成功"
        except Exception as exc:  # noqa: BLE001 — reply failure to the user, never 5xx
            logger.warning("wecom binding consume failed class=%s", exc.__class__.__name__)
            reply_content = "❌ 绑定失败：凭证无效、已过期或已被使用"

    reply = _build_encrypted_reply(
        reply_content, settings=settings, timestamp=timestamp, nonce=nonce
    )
    return Response(reply, media_type="application/xml")

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

from backend.common.database import Pool
from backend.common.security import constant_time_eq
from backend.config import Settings
from backend.dependencies import get_pool, get_settings_dep
from backend.identity import service as identity_service

logger = logging.getLogger("wealthsail.wecom")

router = APIRouter(prefix="/adapters/wecom", tags=["wecom"])

REPLAY_WINDOW_SECONDS = 300

WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

# corp_secret -> (access_token, expires_at_unix)；进程内缓存，有效期 7200s
_token_cache: dict[str, tuple[str, float]] = {}


# ---------------------------------------------------------------------------
# 主动推送（outbox 消费者 / 运营接口调用）
# ---------------------------------------------------------------------------
async def get_access_token(settings: Settings) -> str:
    """获取企业微信 access_token（进程内缓存，提前 300s 刷新）。

    Raises RuntimeError: 凭据无效或接口异常。
    """
    cached = _token_cache.get(settings.wecom_corp_secret)
    if cached and cached[1] > time.time() + 300:
        return cached[0]
    async with httpx.AsyncClient(timeout=15) as client:
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
        raise RuntimeError(f"wecom gettoken failed: errcode={data.get('errcode')} {data.get('errmsg')}")
    token = str(data["access_token"])
    expires_in = int(data.get("expires_in", 7200))
    _token_cache[settings.wecom_corp_secret] = (token, time.time() + expires_in)
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

    优先使用自建应用（gettoken + message/send，agent_id 发送）；
    失败时回退到智能机器人（API 模式·长连接，markdown 主动推送，
    仅在配置了 WECOM_BOT_ID / WECOM_BOT_SECRET 时可用）。

    Args:
        user_id: 企微通讯录成员 userid（touser / 单聊 chatid）。
        content: 消息文本（UTF-8，企微限制 2048 字节）。
        msg_type: 自建应用消息类型（默认 text）。

    Returns:
        msgid（自建应用）或 req_id（智能机器人）。

    Raises:
        RuntimeError: 两种通道均失败（最后一次异常上抛）。
    """
    try:
        return await _send_app_message(settings, user_id, content, msg_type=msg_type)
    except Exception as exc:  # noqa: BLE001 — 自建应用失败尝试机器人回退
        logger.warning("wecom app push failed (%s), trying bot fallback", exc)
        if not (settings.wecom_bot_id and settings.wecom_bot_secret):
            raise
    from backend.integrations.wecom_bot import get_bot_client

    client = get_bot_client(settings.wecom_bot_id, settings.wecom_bot_secret)
    return await client.send_msg(user_id, content)


async def _send_app_message(
    settings: Settings,
    user_id: str,
    content: str,
    *,
    msg_type: str = "text",
) -> str:
    """自建应用（message/send）推送：gettoken + 发送。"""
    token = await get_access_token(settings)
    body: dict[str, Any] = {
        "touser": user_id,
        "msgtype": msg_type,
        "agentid": int(settings.wecom_agent_id),
    }
    if msg_type == "text":
        body["text"] = {"content": content}
    else:
        body[msg_type] = {"content": content}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{WECOM_API_BASE}/message/send",
            params={"access_token": token},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
    if data.get("errcode") != 0:
        raise RuntimeError(
            f"wecom message/send failed: errcode={data.get('errcode')} {data.get('errmsg')}"
        )
    msgid = str(data.get("msgid", ""))
    logger.info("wecom app message sent to %s (msgid=%s)", user_id, msgid)
    return msgid


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


@router.get("/verify")
async def wecom_verify(
    msg_signature: str = Query(default=""),
    timestamp: str = Query(default=""),
    nonce: str = Query(default=""),
    echostr: str = Query(default=""),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """企业微信后台 URL 验证专用接口（公开、无 JWT、无数据库访问，<1s）。

    流程：SHA1 签名校验（token+timestamp+nonce+echostr 字典序）→
    AES-256-CBC 解密（IV=密钥前 16 字节，PKCS7 去填充）→
    解析 16 字节随机串 + 4 字节大端长度 + 消息内容 + 校验尾部 CorpID →
    返回明文（text/plain，无引号无换行）。
    """
    logger.info(
        "wecom verify request: ts=%s nonce=%s sig_len=%d echostr_len=%d",
        timestamp, nonce, len(msg_signature), len(echostr),
    )
    try:
        _check_timestamp(timestamp)
        if not verify_signature(
            settings.wecom_callback_token, timestamp, nonce, echostr, msg_signature
        ):
            logger.warning("wecom verify rejected: signature mismatch")
            return PlainTextResponse("signature mismatch", status_code=403)
        plaintext = wecom_decrypt(
            echostr, settings.wecom_callback_aes_key, settings.wecom_corp_id
        )
    except (ValueError, ET.ParseError) as exc:
        logger.warning("wecom verify failed: %s", exc)
        return PlainTextResponse("verify failed", status_code=403)
    logger.info("wecom verify OK: plaintext_len=%d", len(plaintext))
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
    body = body_bytes.decode("utf-8", errors="replace")

    try:
        envelope = _parse_xml(body)
        encrypt = envelope.get("Encrypt", "")
        _check_timestamp(timestamp)
        if not verify_signature(
            settings.wecom_callback_token, timestamp, nonce, encrypt, msg_signature
        ):
            raise ValueError("signature mismatch")
        inner = _parse_xml(wecom_decrypt(encrypt, settings.wecom_callback_aes_key, settings.wecom_corp_id))
    except (ValueError, ET.ParseError) as exc:
        logger.warning("wecom callback rejected: %s", exc)
        reply = _build_encrypted_reply(
            "回调验证失败", settings=settings, timestamp=timestamp, nonce=nonce
        )
        return Response(reply, media_type="application/xml")

    from_user = inner.get("FromUserName", "")
    content = inner.get("Content", "").strip()
    msg_type = inner.get("MsgType", "")

    reply_content = "收到"
    if msg_type == "text" and content.lower().startswith("/bind"):
        token = content[5:].strip()
        try:
            result = await identity_service.consume_binding_challenge(
                pool,
                settings,
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
            logger.warning("wecom binding consume failed: %s", exc)
            reply_content = f"❌ 绑定失败：{getattr(exc, 'message', str(exc))}"

    reply = _build_encrypted_reply(
        reply_content, settings=settings, timestamp=timestamp, nonce=nonce
    )
    return Response(reply, media_type="application/xml")

"""企业微信智能机器人（API 模式·长连接）客户端。

协议（developer.work.weixin.qq.com/document/path/101463）：
  * wss://openws.work.weixin.qq.com 建立 WebSocket 连接
  * aibot_subscribe  订阅（Bot ID + Secret 身份校验）
  * ping             心跳保活（约 30s）
  * aibot_send_msg   主动推送（单聊 chatid=userid / 群聊 chatid）
  * aibot_msg_callback / aibot_event_callback 接收回调

限制：
  * 同一机器人同时只能保持一个有效长连接（新连接踢旧连接）
  * 主动推送仅支持 markdown / template_card
  * 用户必须先与该机器人会话过，机器人才能主动推送
  * 频率限制 30 条/分钟、1000 条/小时（每个会话）

该通道仅供后续隔离试验，生产发送链路默认不调用。客户端按机器人身份与
凭据版本注册；断线后的未知投递不会自动重发，由上层显式重新建立连接。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import websockets

logger = logging.getLogger("wealthsail.wecom_bot")

WECOM_BOT_WS_URL = "wss://openws.work.weixin.qq.com"

_HEARTBEAT_INTERVAL = 30.0
_SEND_TIMEOUT = 10.0
_MAX_FRAME_SIZE = 1024 * 1024
_MAX_MARKDOWN_BYTES = 4096
_MAX_REGISTERED_CLIENTS = 8

_clients: dict[tuple[str, str], "WeComBotClient"] = {}


class BotDeliveryUnknownError(RuntimeError):
    """The connection ended after send, so provider acceptance is unknown."""


class WeComBotClient:
    """长连接客户端：订阅 + 心跳 + 主动推送（按 req_id 匹配响应）。"""

    def __init__(self, bot_id: str, secret: str) -> None:
        self._bot_id = bot_id
        self._secret = secret
        self._ws: Any = None
        self._lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ 连接
    async def _ensure_connected(self) -> None:
        if self._ws is not None and self._ws.state.name == "OPEN":
            return
        async with self._lock:
            if self._ws is not None and self._ws.state.name == "OPEN":
                return
            await self._connect_once()

    async def _connect_once(self) -> None:
        ws = await websockets.connect(
            WECOM_BOT_WS_URL,
            ping_interval=None,
            max_size=_MAX_FRAME_SIZE,
            open_timeout=_SEND_TIMEOUT,
            close_timeout=_SEND_TIMEOUT,
        )
        req_id = uuid.uuid4().hex
        await ws.send(
            json.dumps(
                {
                    "cmd": "aibot_subscribe",
                    "headers": {"req_id": req_id},
                    "body": {"bot_id": self._bot_id, "secret": self._secret},
                }
            )
        )
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=_SEND_TIMEOUT))
        if resp.get("errcode") != 0:
            await ws.close()
            raise RuntimeError(
                f"aibot_subscribe failed: errcode={resp.get('errcode')}"
            )
        self._ws = ws
        self._pending.clear()
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("wecom bot connected (bot_ref=%s)", _short_ref(self._bot_id))

    async def close(self) -> None:
        tasks = [
            task
            for task in (self._reader_task, self._heartbeat_task)
            if task is not None and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._fail_pending(BotDeliveryUnknownError("wecom bot connection closed"))
        if self._ws is not None:
            await self._ws.close()
        self._ws = None
        self._reader_task = None
        self._heartbeat_task = None

    # ------------------------------------------------------------------ 读循环
    async def _reader_loop(self) -> None:
        while True:
            try:
                raw = await self._ws.recv()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 连接断开进入重连
                logger.warning(
                    "wecom bot connection lost error_class=%s",
                    type(exc).__name__,
                )
                self._ws = None
                self._fail_pending(
                    BotDeliveryUnknownError("wecom bot connection lost")
                )
                if self._heartbeat_task is not None:
                    self._heartbeat_task.cancel()
                return
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                logger.warning("wecom bot non-json frame bytes=%s", len(raw))
                continue
            req_id = (data.get("headers") or {}).get("req_id")
            if req_id and req_id in self._pending:
                fut = self._pending.pop(req_id)
                if not fut.done():
                    fut.set_result(data)
            elif data.get("cmd") in ("aibot_msg_callback", "aibot_event_callback"):
                # 接收回调：记录日志；后续可接入 chat 服务
                logger.info(
                    "wecom bot callback: cmd=%s type=%s sender_ref=%s",
                    data.get("cmd"),
                    (data.get("body") or {}).get("msgtype"),
                    _short_ref(
                        str(
                            ((data.get("body") or {}).get("from") or {}).get(
                                "userid", ""
                            )
                        )
                    ),
                )

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    # ------------------------------------------------------------------ 心跳
    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            try:
                if self._ws is not None and self._ws.state.name == "OPEN":
                    await self._ws.send(
                        json.dumps(
                            {"cmd": "ping", "headers": {"req_id": uuid.uuid4().hex}}
                        )
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 心跳失败交给 reader 重连
                logger.warning(
                    "wecom bot heartbeat failed error_class=%s",
                    type(exc).__name__,
                )
                return

    # ------------------------------------------------------------------ 发送
    async def send_msg(self, userid: str, content: str) -> str:
        """主动推送 markdown 消息到单聊会话（chatid=userid）。返回 req_id。"""
        if not userid.strip():
            raise ValueError("wecom bot recipient must not be empty")
        if len(content.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
            raise ValueError(
                f"wecom bot markdown exceeds {_MAX_MARKDOWN_BYTES} UTF-8 bytes"
            )
        await self._ensure_connected()
        async with self._lock:
            req_id = uuid.uuid4().hex
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[req_id] = fut
            try:
                await self._ws.send(
                    json.dumps(
                        {
                            "cmd": "aibot_send_msg",
                            "headers": {"req_id": req_id},
                            "body": {
                                "chatid": userid,
                                "chat_type": 1,
                                "msgtype": "markdown",
                                "markdown": {"content": content},
                            },
                        }
                    )
                )
                try:
                    resp = await asyncio.wait_for(fut, timeout=_SEND_TIMEOUT)
                except asyncio.TimeoutError as exc:
                    raise BotDeliveryUnknownError(
                        "wecom bot delivery result is unknown"
                    ) from exc
            finally:
                self._pending.pop(req_id, None)
        if resp.get("errcode") != 0:
            raise RuntimeError(
                f"aibot_send_msg failed: errcode={resp.get('errcode')}"
            )
        logger.info(
            "wecom bot message accepted recipient_ref=%s (req=%s)",
            _short_ref(userid),
            req_id,
        )
        return req_id


def _short_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def get_bot_client(
    bot_id: str,
    secret: str,
    credential_version: str = "v1",
) -> WeComBotClient:
    """Return a bounded client keyed by non-secret identity and key version."""
    key = (_short_ref(bot_id), credential_version)
    client = _clients.get(key)
    if client is not None:
        return client
    if len(_clients) >= _MAX_REGISTERED_CLIENTS:
        raise RuntimeError("wecom bot client registry is full")
    client = WeComBotClient(bot_id, secret)
    _clients[key] = client
    return client


async def close_bot_clients() -> None:
    """Close every experimental client during an owning process shutdown."""
    clients = list(_clients.values())
    _clients.clear()
    if clients:
        await asyncio.gather(*(client.close() for client in clients))


class _OpportunityWebhookLogFilter(logging.Filter):
    """HTTPX normally logs the full request URL, including webhook keys."""

    def filter(self, record):
        message = record.getMessage()
        if "qyapi.weixin.qq.com" in message and "/webhook/send?" in message:
            record.msg = re.sub(
                r"(https://qyapi\.weixin\.qq\.com(?::443)?/cgi-bin/webhook/send\?)[^\s\"']+",
                r"\1[redacted]",
                message,
            )
            record.args = ()
        return True


logging.getLogger("httpx").addFilter(_OpportunityWebhookLogFilter())


def _opportunity_webhook_url(settings):
    from backend.integrations.wecom import DeliveryRejectedError

    value = getattr(settings, "wecom_opportunity_webhook_url", "")
    try:
        url = urlsplit(value)
        query = parse_qs(url.query, strict_parsing=True, keep_blank_values=True)
        valid = (
            url.scheme == "https"
            and url.hostname == "qyapi.weixin.qq.com"
            and url.port in {None, 443}
            and url.username is None
            and url.password is None
            and url.path == "/cgi-bin/webhook/send"
            and not url.fragment
            and set(query) == {"key"}
            and len(query["key"]) == 1
            and bool(query["key"][0].strip())
            and not any(character.isspace() for character in value)
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise DeliveryRejectedError("opportunity webhook routing is not approved")
    return value


async def send_opportunity_webhook(settings, markdown, *, client=None):
    """One explicitly configured group-webhook POST; never retry or fall back."""
    from backend.integrations.wecom import (
        DeliveryRejectedError,
        DeliveryUnknownError,
        get_http_client,
    )

    url = _opportunity_webhook_url(settings)
    if not isinstance(markdown, str) or not markdown.strip():
        raise DeliveryRejectedError("opportunity webhook content is empty")
    if len(markdown.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
        raise DeliveryRejectedError("opportunity webhook content exceeds UTF-8 limit")
    client = client if client is not None else await get_http_client()
    try:
        response = await client.post(
            url,
            json={"msgtype": "markdown", "markdown": {"content": markdown}},
            follow_redirects=False,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
    except (httpx.TimeoutException, httpx.TransportError):
        raise DeliveryUnknownError("opportunity webhook delivery is unknown") from None
    if 400 <= response.status_code < 500:
        raise DeliveryRejectedError("opportunity webhook HTTP rejection")
    if not 200 <= response.status_code < 300:
        raise DeliveryUnknownError("opportunity webhook HTTP result is unknown")
    try:
        body = response.json()
    except (ValueError, TypeError):
        raise DeliveryUnknownError("opportunity webhook response is invalid") from None
    code = body.get("errcode") if isinstance(body, dict) else None
    if type(code) is not int:
        raise DeliveryUnknownError("opportunity webhook acceptance is unknown")
    if code != 0:
        raise DeliveryRejectedError("opportunity webhook provider rejected the message")
    logger.info("opportunity_webhook_accepted")
    return "accepted"

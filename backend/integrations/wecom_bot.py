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

连接由模块级单例懒创建并缓存：首次 send 时建连，断线自动指数退避重连。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import websockets

logger = logging.getLogger("wealthsail.wecom_bot")

WECOM_BOT_WS_URL = "wss://openws.work.weixin.qq.com"

_HEARTBEAT_INTERVAL = 30.0
_SEND_TIMEOUT = 10.0
_RECONNECT_BASE = 2.0
_RECONNECT_MAX = 60.0

_client: "WeComBotClient | None" = None


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
        ws = await websockets.connect(WECOM_BOT_WS_URL, ping_interval=None, max_size=None)
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
                f"aibot_subscribe failed: errcode={resp.get('errcode')} {resp.get('errmsg')}"
            )
        self._ws = ws
        self._pending.clear()
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("wecom bot connected (bot_id=%s)", self._bot_id)

    async def close(self) -> None:
        for task in (self._reader_task, self._heartbeat_task):
            if task is not None:
                task.cancel()
        if self._ws is not None:
            await self._ws.close()
        self._ws = None

    # ------------------------------------------------------------------ 读循环
    async def _reader_loop(self) -> None:
        backoff = _RECONNECT_BASE
        while True:
            try:
                raw = await self._ws.recv()
                backoff = _RECONNECT_BASE
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 连接断开进入重连
                logger.warning("wecom bot connection lost: %s", exc)
                await self._teardown()
                try:
                    await asyncio.sleep(backoff)
                    await self._connect_once()
                except Exception:  # noqa: BLE001
                    backoff = min(backoff * 2, _RECONNECT_MAX)
                    continue
                backoff = _RECONNECT_BASE
                continue
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                logger.warning("wecom bot non-json frame: %r", raw[:200])
                continue
            req_id = (data.get("headers") or {}).get("req_id")
            if req_id and req_id in self._pending:
                fut = self._pending.pop(req_id)
                if not fut.done():
                    fut.set_result(data)
            elif data.get("cmd") in ("aibot_msg_callback", "aibot_event_callback"):
                # 接收回调：记录日志；后续可接入 chat 服务
                logger.info(
                    "wecom bot callback: cmd=%s type=%s from=%s",
                    data.get("cmd"),
                    (data.get("body") or {}).get("msgtype"),
                    ((data.get("body") or {}).get("from") or {}).get("userid"),
                )

    async def _teardown(self) -> None:
        for task in (self._reader_task, self._heartbeat_task):
            if task is not None and not task.done():
                task.cancel()
        self._reader_task = None
        self._heartbeat_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._ws = None

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
                logger.warning("wecom bot heartbeat failed: %s", exc)

    # ------------------------------------------------------------------ 发送
    async def send_msg(self, userid: str, content: str) -> str:
        """主动推送 markdown 消息到单聊会话（chatid=userid）。返回 req_id。"""
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
                resp = await asyncio.wait_for(fut, timeout=_SEND_TIMEOUT)
            finally:
                self._pending.pop(req_id, None)
        if resp.get("errcode") != 0:
            raise RuntimeError(
                f"aibot_send_msg failed: errcode={resp.get('errcode')} {resp.get('errmsg')}"
            )
        logger.info("wecom bot message sent to %s (req=%s)", userid, req_id)
        return req_id


def get_bot_client(bot_id: str, secret: str) -> WeComBotClient:
    """模块级单例（同一机器人同时只能一个长连接）。"""
    global _client
    if _client is None:
        _client = WeComBotClient(bot_id, secret)
    return _client

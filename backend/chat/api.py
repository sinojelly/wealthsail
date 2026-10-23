"""Chat API: POST /api/v1/chat/stream — SSE streaming endpoint."""

from __future__ import annotations

from contextlib import aclosing
import logging
from typing import AsyncGenerator

import anyio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.chat.service import ChatService, ChatRequest
from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import (
    get_pool,
    get_settings_dep,
    get_tenant_context,
)

logger = logging.getLogger("wealthsail.chat_api")

router = APIRouter(prefix="/chat", tags=["chat"])


class ClosingStreamingResponse(StreamingResponse):
    """Close a body suspended at yield when ASGI sending fails or disconnects."""

    async def __call__(self, scope, receive, send):
        try:
            return await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                close = getattr(self.body_iterator, "aclose", None)
                if close is not None:
                    await close()


class ChatMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="用户消息内容")
    model: str | None = Field(default=None, description="可选覆盖模型名称")


def _resolve_model(requested: str | None, configured: str) -> str:
    """Allow the configured production model only; callers cannot choose cost."""
    if requested is None or requested == configured:
        return configured
    raise ValueError("requested chat model is not allowed")


@router.post("/stream", response_model=None)
async def chat_stream(
    body: ChatMessage,
    pool: Pool = Depends(get_pool),
    ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings_dep),
) -> StreamingResponse:
    """流式 AI 对话。

    接收用户文本消息，注入持仓/报告/市场情绪上下文后调用 LLM，
    以 Server-Sent Events 格式逐 chunk 返回。

    每行 SSE 格式:
      data: {"delta": "文本片段"}\n\n
      data: {"done": true}\n\n
    """
    chat_svc = ChatService(settings)
    try:
        model = _resolve_model(body.model, settings.codex_model or "codex")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="请求的模型未获准使用") from exc

    async def _event_stream() -> AsyncGenerator[str, None]:
        # Only immutable tenant context is read inside the transaction. Market,
        # news and model network calls happen after the connection is released.
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            database_context = await chat_svc.read_database_context(
                conn, ctx.tenant_id
            )
        system_context = await chat_svc.build_system_context(
            database_context, body.message
        )
        request = ChatRequest(
            user_id=ctx.user_id,
            tenant_id=ctx.tenant_id,
            message=body.message,
            model=model,
        )
        async with aclosing(chat_svc.stream_completion(request, system_context)) as stream:
            async for chunk in stream:
                yield chunk

    return ClosingStreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 兼容 Nginx 反向代理的流式缓冲
            "Connection": "keep-alive",
        },
    )

"""Chat API: POST /api/v1/chat/stream — SSE streaming endpoint."""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.chat.service import ChatService, ChatRequest
from backend.common.database import Pool, tenant_transaction
from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
    now_shanghai,
)

logger = logging.getLogger("wealthsail.chat_api")

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="用户消息内容")
    model: str | None = Field(default=None, description="可选覆盖模型名称")


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

    async def _event_stream() -> AsyncGenerator[str, None]:
        async with tenant_transaction(pool, ctx.user_id, ctx.tenant_id) as conn:
            request = ChatRequest(
                user_id=ctx.user_id,
                tenant_id=ctx.tenant_id,
                message=body.message,
                model=body.model,
            )
            async for chunk in chat_svc.stream_completion(conn, request):
                yield chunk

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 兼容 Nginx 反向代理的流式缓冲
            "Connection": "keep-alive",
        },
    )

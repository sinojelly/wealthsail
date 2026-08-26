"""DeepSeek (OpenAI-compatible) model gateway.

The Worker uses this for four-session report generation; the API uses it for
on-demand report generation. When MODEL_API_KEY is not configured the caller
falls back to a deterministic local template (model_run status ABSTAINED).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.config import Settings

logger = logging.getLogger("wealthsail.model_gateway")


class ModelNotConfiguredError(Exception):
    """Raised when no LLM API key is configured."""


class ModelCallError(Exception):
    """Raised when the LLM provider fails after retries."""


@dataclass(frozen=True)
class ModelResult:
    content: str
    model: str
    usage: dict[str, int]
    cost_cny: Decimal


@retry(
    retry=retry_if_exception_type(httpx.HTTPStatusError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _post_with_retry(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any]
) -> httpx.Response:
    response = await client.post(url, json=payload)
    response.raise_for_status()
    return response


async def chat_completion(
    *,
    settings: Settings,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    json_mode: bool = True,
) -> ModelResult:
    """Call the configured chat model; returns parsed completion."""
    if not settings.model_api_key:
        raise ModelNotConfiguredError("MODEL_API_KEY 未配置")

    url = settings.model_base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "model": settings.model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {settings.model_api_key}"}
    timeout = httpx.Timeout(settings.model_timeout_seconds)

    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            response = await _post_with_retry(client, url, payload)
    except httpx.HTTPStatusError as exc:
        logger.error("model gateway rejected request: status=%s", exc.response.status_code)
        raise ModelCallError(f"模型服务返回 {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        logger.error("model gateway transport error: %s", exc)
        raise ModelCallError("模型服务连接失败") from exc

    body = response.json()
    content = body["choices"][0]["message"]["content"]
    usage = body.get("usage", {})
    return ModelResult(
        content=content,
        model=body.get("model", settings.model_name),
        usage={
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "completion_tokens": int(usage.get("completion_tokens", 0)),
        },
        cost_cny=Decimal("0.0000"),
    )

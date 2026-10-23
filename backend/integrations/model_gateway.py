"""Report model gateway using only Codex, with no provider fallback or retry."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException

from backend.config import Settings
from backend.integrations import codex_client


class ModelNotConfiguredError(Exception):
    """Raised when Codex is not configured or authenticated."""


class ModelCallError(Exception):
    """Raised when one Codex invocation fails."""


@dataclass(frozen=True)
class ModelResult:
    content: str
    model: str
    usage: dict[str, int]
    cost_cny: Decimal


async def chat_completion(
    *,
    settings: Settings,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    json_mode: bool = True,
) -> ModelResult:
    """Keep the existing report interface while routing every model call to Codex.

    ``temperature`` is retained for callers; Codex selects supported inference
    parameters. No legacy provider credential or URL is read.
    """
    try:
        body = await codex_client.completion(
            settings, system_prompt=system_prompt, user_prompt=user_prompt,
            json_mode=json_mode,
        )
        usage = body.get("usage") or {}
        return ModelResult(
            content=body["content"], model=body.get("model") or "codex",
            usage={
                "prompt_tokens": int(usage.get("prompt_tokens", usage.get("input_tokens", 0))),
                "completion_tokens": int(usage.get("completion_tokens", usage.get("output_tokens", 0))),
            },
            cost_cny=Decimal(str(body.get("cost_cny", "0.0000"))),
        )
    except codex_client.CodexNotConfiguredError as exc:
        raise ModelNotConfiguredError("Codex 尚未配置或未完成认证") from exc
    except (codex_client.CodexCallError, ValueError, TypeError, KeyError,
            AttributeError, DecimalException, TimeoutError) as exc:
        raise ModelCallError("Codex 报告生成暂时不可用") from exc

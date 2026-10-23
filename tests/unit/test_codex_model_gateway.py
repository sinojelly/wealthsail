"""Report callers keep their interface while every model execution uses Codex."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.integrations import codex_client, model_gateway


@pytest.mark.asyncio
async def test_report_gateway_uses_codex_without_any_model_api_key(monkeypatch):
    call = AsyncMock(return_value={"content": '{"summary":"report"}', "model": "approved-codex", "usage": {"input_tokens": 12, "output_tokens": 5}, "cost_cny": "0.0000"})
    monkeypatch.setattr(codex_client, "completion", call)
    settings = SimpleNamespace(model_api_key=None, codex_model=None)
    result = await model_gateway.chat_completion(settings=settings, system_prompt="tenant facts", user_prompt="report")
    assert result.content == '{"summary":"report"}'
    assert result.model == "approved-codex"
    assert result.usage == {"prompt_tokens": 12, "completion_tokens": 5}
    assert result.cost_cny == Decimal("0.0000")
    call.assert_awaited_once_with(settings, system_prompt="tenant facts", user_prompt="report", json_mode=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [(codex_client.CodexNotConfiguredError("safe"), model_gateway.ModelNotConfiguredError), (codex_client.CodexCallError("safe"), model_gateway.ModelCallError)])
async def test_report_gateway_maps_safe_errors_without_provider_retry(monkeypatch, error, expected):
    call = AsyncMock(side_effect=error)
    monkeypatch.setattr(codex_client, "completion", call)
    with pytest.raises(expected):
        await model_gateway.chat_completion(settings=SimpleNamespace(), system_prompt="scope", user_prompt="text")
    assert call.await_count == 1

"""Chat must keep external work outside DB transactions and fail closed."""

import pytest

from backend.chat.api import _resolve_model


def test_chat_model_override_is_not_an_arbitrary_provider_passthrough() -> None:
    assert _resolve_model(None, "deepseek-chat") == "deepseek-chat"
    assert _resolve_model("deepseek-chat", "deepseek-chat") == "deepseek-chat"
    with pytest.raises(ValueError, match="not allowed"):
        _resolve_model("expensive-unreviewed-model", "deepseek-chat")

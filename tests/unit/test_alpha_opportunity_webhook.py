"""The opt-in opportunity webhook sends once and never exposes its key."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import logging

import httpx
import pytest

from backend.integrations import wecom_bot
from backend.integrations.wecom import DeliveryRejectedError, DeliveryUnknownError


def sender():
    assert hasattr(wecom_bot, "send_opportunity_webhook"), (
        "opportunity webhook sender missing"
    )
    return wecom_bot.send_opportunity_webhook


def settings(
    url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=private-fixture-key",
):
    return SimpleNamespace(wecom_opportunity_webhook_url=url)


@pytest.mark.asyncio
async def test_one_post_uses_markdown_without_logging_the_secret(caplog):
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    caplog.set_level(logging.INFO, logger="httpx")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert (
            await sender()(settings(), "价格核对，不执行交易。", client=client)
            == "accepted"
        )
    assert len(calls) == 1
    assert calls[0].method == "POST"
    assert b'"msgtype":"markdown"' in calls[0].content
    assert "private-fixture-key" not in caplog.text


@pytest.mark.parametrize(
    "url",
    [
        "http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
        "https://evil.example/cgi-bin/webhook/send?key=x",
        "https://qyapi.weixin.qq.com.evil.example/cgi-bin/webhook/send?key=x",
        "https://qyapi.weixin.qq.com:444/cgi-bin/webhook/send?key=x",
        "https://name:password@qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x",
        "https://qyapi.weixin.qq.com/cgi-bin/message/send?key=x",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x&key=y",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x&next=private",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x#fragment",
    ],
)
@pytest.mark.asyncio
async def test_unapproved_routing_is_rejected_before_network(url):
    client = AsyncMock()
    with pytest.raises(DeliveryRejectedError):
        await sender()(settings(url), "核对", client=client)
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_utf8_limit_is_checked_before_network():
    client = AsyncMock()
    with pytest.raises(DeliveryRejectedError):
        await sender()(settings(), "价" * 1366, client=client)
    client.post.assert_not_awaited()


@pytest.mark.parametrize(
    "outcome,expected",
    [
        (httpx.ReadTimeout("secret provider text"), DeliveryUnknownError),
        (httpx.Response(503, json={}), DeliveryUnknownError),
        (
            httpx.Response(302, headers={"location": "https://evil.example"}),
            DeliveryUnknownError,
        ),
        (httpx.Response(400, json={}), DeliveryRejectedError),
        (httpx.Response(200, json={"errcode": 93000}), DeliveryRejectedError),
        (httpx.Response(200, json={}), DeliveryUnknownError),
    ],
)
@pytest.mark.asyncio
async def test_provider_failure_never_retries_or_changes_channel(outcome, expected):
    calls = []

    async def handle(request):
        calls.append(request)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(expected) as caught:
            await sender()(settings(), "核对", client=client)
    assert len(calls) == 1
    assert "private-fixture-key" not in str(caught.value)
    assert "secret provider text" not in str(caught.value)

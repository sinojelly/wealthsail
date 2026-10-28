"""Account diagnostics must survive the real Runner logging entry point."""

import logging
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.integrations import codex_client, codex_private
from backend.integrations.codex_account_cache import (
    AccountMetadataCache,
    account_diagnostic,
)


def test_runner_entry_point_emits_application_account_stages_without_real_server():
    # A clean interpreter reproduces root's default WARNING level, which caplog
    # otherwise changes. Stub both app creation and Uvicorn: no socket, CLI or RPC.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import time
import uvicorn
from backend import codex_runner
from backend.integrations.codex_account_cache import account_diagnostic
codex_runner.create_app = lambda: object()
uvicorn.run = lambda *args, **kwargs: account_diagnostic('initialize', time.monotonic())
codex_runner.main()
""",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert "Codex account stage=initialize" in result.stderr


@pytest.mark.asyncio
async def test_successful_transport_reports_unavailable_account_status(
    monkeypatch, caplog
):
    async def request(*args, **kwargs):
        return codex_private.unavailable_account()

    monkeypatch.setattr(codex_client, "_request_json", request)
    with caplog.at_level(logging.INFO):
        result = await codex_client.account(
            SimpleNamespace(
                codex_runner_url="http://runner.test",
                codex_runner_token="fixture-only",
            ),
            fresh=True,
        )
    assert result["status"] == "UNAVAILABLE"
    transport = next(
        row.message for row in caplog.records if "stage=transport" in row.message
    )
    assert "error=NONE" in transport
    assert "status=UNAVAILABLE" in transport


@pytest.mark.asyncio
async def test_cache_log_distinguishes_handled_unavailable_from_ready(caplog):
    async def fetch():
        return codex_private.unavailable_account()

    subject = AccountMetadataCache(fetch, generation=lambda: "fixture-generation")
    with caplog.at_level(logging.INFO):
        await subject.get(fresh=True)
    message = next(
        row.message for row in caplog.records if "stage=cache_fetch" in row.message
    )
    assert "status=UNAVAILABLE" in message and "error=NONE" in message


@pytest.mark.parametrize("status", ["READY", "STALE", "UNAVAILABLE", "UNAUTHENTICATED"])
def test_account_diagnostic_only_exposes_allowed_status(status, caplog):
    with caplog.at_level(logging.INFO):
        account_diagnostic("transport", 0, status=status)
    assert f"status={status}" in caplog.text


@pytest.mark.parametrize(
    "status", ["private@example.test secret raw response", [], {}, None]
)
def test_unknown_status_is_redacted_without_logging_arbitrary_values(status, caplog):
    with caplog.at_level(logging.INFO):
        account_diagnostic("transport", 0, status=status)
    assert "status=UNKNOWN" in caplog.text
    assert "private@example.test" not in caplog.text and "secret" not in caplog.text

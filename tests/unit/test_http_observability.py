"""HTTP boundary diagnostics that are safe to retain in production logs."""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from backend.main import app


def test_method_mismatch_logs_proxy_context_without_query_secrets(caplog) -> None:
    """A 405 must expose enough context to locate proxy method rewrites safely."""
    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(logging.INFO, logger="wealthsail.http"):
        response = client.get(
            "/api/v1/auth/register?invitationToken=must-not-appear-in-logs",
            headers={
                "Host": "wealthsail.example",
                "X-Request-Id": "req_diagnostic_405",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "wealthsail.example",
            },
        )

    assert response.status_code == 405
    assert response.headers["Allow"] == "POST"
    assert response.json()["error"] == {
        "code": "VALIDATION_ERROR",
        "message": "请求方法不受支持",
        "retryable": False,
        "fieldErrors": [],
    }

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "wealthsail.http"
    ]
    assert len(messages) == 1
    message = messages[0]
    assert "method=GET" in message
    assert "path=/api/v1/auth/register" in message
    assert "status=405" in message
    assert "request_id=req_diagnostic_405" in message
    assert "allow=POST" in message
    assert "host=wealthsail.example" in message
    assert "forwarded_proto=https" in message
    assert "forwarded_host=wealthsail.example" in message
    assert "must-not-appear-in-logs" not in message

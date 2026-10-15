"""Source diagnostics expose numeric status without exception text or secrets."""

from datetime import datetime, timezone
import json

import httpx
import pytest

from backend.integrations import opportunity_sources as source


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 429])
async def test_http_status_is_retained_separately_from_error_class(monkeypatch, status):
    async def blocked(client, uri):
        request = httpx.Request("GET", "https://www.pbc.gov.cn/?token=secret")
        raise httpx.HTTPStatusError(
            "private diagnostic secret",
            request=request,
            response=httpx.Response(status, request=request),
        )

    monkeypatch.setattr(source, "_fetch_text", blocked)
    data = await source.fetch_opportunity_series(
        datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    )
    for key in ("PBOC_M1", "PBOC_TSF", "PBOC_LOANS"):
        assert data["sourceResults"][key] == {
            "status": "FAILED",
            "errorClass": "HTTPStatusError",
            "httpStatus": status,
        }
    assert "secret" not in json.dumps(data)
    assert data["sourceResults"]["CHINABOND"]["status"] == "PERMISSION_REQUIRED"


@pytest.mark.asyncio
async def test_timeout_is_not_misrepresented_as_http_rejection(monkeypatch):
    async def timed_out(client, uri):
        raise httpx.ReadTimeout("private diagnostic secret")

    monkeypatch.setattr(source, "_fetch_text", timed_out)
    data = await source.fetch_opportunity_series(
        datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    )
    assert data["sourceResults"]["PBOC_M1"] == {
        "status": "FAILED",
        "errorClass": "ReadTimeout",
    }
    assert "secret" not in json.dumps(data)

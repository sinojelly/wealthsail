"""Opt-in live API acceptance test for the critical delivery workflow.

This test mutates holdings and may send a real WeCom message. It is skipped
unless every WEALTHSAIL_LIVE_E2E_* variable is explicitly supplied. Keep the
credentials in CI/server secrets, never in source control.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass

import httpx
import pytest


@dataclass(frozen=True)
class LiveConfig:
    base_url: str
    email: str
    password: str


@pytest.fixture(scope="module")
def live_config() -> LiveConfig:
    enabled = os.environ.get("WEALTHSAIL_LIVE_E2E") == "1"
    base_url = os.environ.get("WEALTHSAIL_LIVE_E2E_BASE_URL", "").rstrip("/")
    email = os.environ.get("WEALTHSAIL_LIVE_E2E_EMAIL", "")
    password = os.environ.get("WEALTHSAIL_LIVE_E2E_PASSWORD", "")
    if not (enabled and base_url and email and password):
        pytest.skip("live E2E is disabled or its secret inputs are incomplete")
    return LiveConfig(base_url, email, password)


@pytest.fixture(scope="module")
def client(live_config: LiveConfig):
    with httpx.Client(
        base_url=f"{live_config.base_url}/api/v1", timeout=90
    ) as value:
        yield value


@pytest.fixture(scope="module")
def authenticated(client: httpx.Client, live_config: LiveConfig) -> dict:
    response = client.post(
        "/auth/login",
        json={"email": live_config.email, "password": live_config.password},
    )
    assert response.status_code == 200, response.text[:500]
    data = response.json()["data"]
    assert data["accessToken"]
    client.headers["Authorization"] = f"Bearer {data['accessToken']}"
    return data


def test_login_returns_a_usable_access_token(authenticated: dict) -> None:
    assert authenticated["accessToken"]


@pytest.fixture(scope="module")
def confirmed_import(client: httpx.Client, authenticated: dict) -> dict:
    correlation = uuid.uuid4().hex
    response = client.post(
        "/portfolio/imports",
        headers={"Idempotency-Key": f"live-e2e-import-{correlation}"},
        json={
            "symbol": os.environ.get("WEALTHSAIL_LIVE_E2E_SYMBOL", "510300"),
            "assetType": "ETF",
            "quantity": "1",
            "avgCost": "4.68",
        },
    )
    assert response.status_code == 201, response.text[:500]
    draft = response.json()["data"]
    assert draft["status"] == "DRAFT"

    response = client.post(
        f"/portfolio/imports/{draft['batchId']}/confirm",
        headers={"Idempotency-Key": f"live-e2e-confirm-{correlation}"},
    )
    assert response.status_code == 200, response.text[:500]
    return response.json()["data"]


def test_holding_import_reaches_confirmed(
    confirmed_import: dict,
) -> None:
    assert confirmed_import["status"] == "CONFIRMED"
    assert confirmed_import["batchId"]


@pytest.fixture(scope="module")
def generated_report(client: httpx.Client, confirmed_import: dict) -> dict:
    response = client.post("/reports/generate", json={"session": "POST_MARKET"})
    assert response.status_code == 200, response.text[:500]
    return response.json()["data"]


def test_report_generation_persists_a_report(generated_report: dict) -> None:
    assert generated_report["id"]
    assert generated_report["session"] == "POST_MARKET"


def test_outbox_delivery_reaches_sent(
    client: httpx.Client, generated_report: dict
) -> None:
    deadline = time.monotonic() + 90
    last_payload: dict = {}
    while time.monotonic() < deadline:
        response = client.get("/operations/outbox")
        assert response.status_code == 200, response.text[:500]
        last_payload = response.json()["data"]
        matching = [
            item
            for item in last_payload["recent"]
            if item["aggregateId"] == generated_report["id"]
        ]
        if matching and all(item["status"] == "SENT" for item in matching):
            return
        if any(item["status"] in {"DEAD", "UNKNOWN"} for item in matching):
            pytest.fail(f"outbox entered terminal failure: {matching}")
        time.sleep(3)
    pytest.fail(f"outbox did not reach SENT within 90s: {last_payload}")

"""Queue cancellation cannot start work or cross the current actor boundary."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from tests.unit.test_research_debug_api import TENANT, USER, client

PATH = "/api/v1/investment/daily-research/refresh"


def mocks(monkeypatch, module, request_id, *, mode="DEBUG", status="CANCELLED"):
    value = {"id": str(request_id), "requestMode": mode, "status": status}
    read = AsyncMock(return_value={**value, "status": "QUEUED"})
    cancel = AsyncMock(return_value=value)
    monkeypatch.setattr(module.repository, "read_refresh", read)
    monkeypatch.setattr(module.repository, "cancel_refresh", cancel, raising=False)
    return read, cancel


def test_owner_can_cancel_only_authenticated_scoped_unstarted_request(monkeypatch):
    api, module = client(monkeypatch)
    identity = uuid4()
    read, cancel = mocks(monkeypatch, module, identity)
    enqueue = AsyncMock()
    monkeypatch.setattr(module.repository, "enqueue_refresh", enqueue)
    response = api.post(f"{PATH}/{identity}/cancel", json={})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "CANCELLED"
    assert read.call_args.args[1:4] == (UUID(TENANT), UUID(USER), identity)
    assert cancel.call_args.args[1:4] == (UUID(TENANT), UUID(USER), identity)
    assert not enqueue.called


@pytest.mark.parametrize(
    "options",
    [
        {"role": "ADMIN"},
        {"role": "VIEWER"},
        {"active": False},
        {"enabled": False},
        {"daily": False},
        {"env": "production"},
    ],
)
def test_cancel_requires_current_owner_and_enabled_environment(monkeypatch, options):
    api, module = client(monkeypatch, **options)
    identity = uuid4()
    _, cancel = mocks(monkeypatch, module, identity)
    assert api.post(f"{PATH}/{identity}/cancel", json={}).status_code == 403
    cancel.assert_not_called()


def test_disabled_saved_request_mode_cannot_be_cancelled(monkeypatch):
    api, module = client(monkeypatch)
    identity = uuid4()
    _, cancel = mocks(monkeypatch, module, identity, mode="MANUAL")
    assert api.post(f"{PATH}/{identity}/cancel", json={}).status_code == 403
    cancel.assert_not_called()


def test_running_request_cancellation_reports_conflict_without_new_dispatch(
    monkeypatch,
):
    from backend.errors import VersionConflictError

    api, module = client(monkeypatch)
    identity = uuid4()
    _, cancel = mocks(monkeypatch, module, identity)
    cancel.side_effect = VersionConflictError("请求已开始，不能取消排队")
    assert api.post(f"{PATH}/{identity}/cancel", json={}).status_code == 409


def test_cancel_rejects_client_actor_stage_and_process_fields(monkeypatch):
    api, module = client(monkeypatch)
    identity = uuid4()
    _, cancel = mocks(monkeypatch, module, identity)
    response = api.post(f"{PATH}/{identity}/cancel", json={"userId": USER, "pid": 42})
    assert response.status_code == 400
    cancel.assert_not_called()

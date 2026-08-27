"""HTTP contract tests for the recoverable Tiantian Fund import workflow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.common.tenancy import TenantContext
from backend.config import Settings
from backend.dependencies import (
    get_pool,
    get_request_id,
    get_settings_dep,
    get_tenant_context,
)
from backend.errors import MoneyJSONResponse, VersionConflictError, register_exception_handlers
from backend.portfolio import api as portfolio_api


def _client(
    *,
    enabled: bool = True,
    role: str = "EDITOR",
    max_upload_bytes: int = 1024 * 1024,
) -> TestClient:
    app = FastAPI(default_response_class=MoneyJSONResponse)
    register_exception_handlers(app)
    app.include_router(portfolio_api.router, prefix="/api/v1")
    settings = Settings(
        _env_file=None,
        app_env="test",
        tiantian_import_enabled=enabled,
        tiantian_import_max_upload_bytes=max_upload_bytes,
    )
    app.state.settings = settings
    app.dependency_overrides[get_pool] = lambda: object()
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id="00000000-0000-0000-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000002",
        tenant_role=role,
    )
    app.dependency_overrides[get_request_id] = lambda: "req_tiantian_api"
    app.dependency_overrides[get_settings_dep] = lambda: settings
    return TestClient(app, raise_server_exceptions=False)


def test_preview_streams_zip_to_deleted_temporary_file() -> None:
    observed: dict[str, object] = {}

    async def preview(pool, ctx, upload, account_id, account_name, idempotency_key, *, limits):
        observed.update(
            path=upload,
            exists=upload.exists(),
            content=upload.read_bytes(),
            account_id=account_id,
            account_name=account_name,
            key=idempotency_key,
            max_compressed_bytes=limits.max_compressed_bytes,
        )
        return {"batchId": "batch-1", "status": "DRAFT", "version": 1}

    with patch(
        "backend.portfolio.api.tiantian_import_service.preview_tiantian_import",
        side_effect=preview,
    ):
        response = _client().post(
            "/api/v1/portfolio/imports/tiantian/preview",
            headers={"Idempotency-Key": "preview-1"},
            data={"accountName": "天天基金账户"},
            files={"file": ("wealthsail-tiantian.zip", b"PK\x03\x04payload", "application/zip")},
        )

    assert response.status_code == 201
    assert response.json()["data"]["batchId"] == "batch-1"
    assert observed["exists"] is True
    assert observed["content"] == b"PK\x03\x04payload"
    assert observed["account_name"] == "天天基金账户"
    assert observed["key"] == "preview-1"
    assert observed["max_compressed_bytes"] == 1024 * 1024
    assert not Path(observed["path"]).exists()


@pytest.mark.parametrize(
    ("filename", "content", "max_bytes"),
    [
        ("snapshot.txt", b"PK\x03\x04payload", 1024),
        ("snapshot.zip", b"PK\x03\x04" + b"x" * 2048, 1024),
        ("snapshot.zip", b"", 1024),
    ],
)
def test_preview_rejects_invalid_upload_before_parsing(
    filename: str, content: bytes, max_bytes: int
) -> None:
    with patch(
        "backend.portfolio.api.tiantian_import_service.preview_tiantian_import",
        new_callable=AsyncMock,
    ) as preview:
        response = _client(max_upload_bytes=max_bytes).post(
            "/api/v1/portfolio/imports/tiantian/preview",
            headers={"Idempotency-Key": "preview-invalid"},
            data={"accountName": "天天基金账户"},
            files={"file": (filename, content, "application/octet-stream")},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    preview.assert_not_awaited()


def test_preview_always_removes_temporary_file_when_parser_fails() -> None:
    observed_path: Path | None = None

    async def fail(*args, **kwargs):
        nonlocal observed_path
        observed_path = args[2]
        assert observed_path.exists()
        raise ValueError("provider-private-body")

    with patch(
        "backend.portfolio.api.tiantian_import_service.preview_tiantian_import",
        side_effect=fail,
    ):
        response = _client().post(
            "/api/v1/portfolio/imports/tiantian/preview",
            headers={"Idempotency-Key": "preview-failure"},
            data={"accountName": "天天基金账户"},
            files={"file": ("snapshot.zip", b"PK\x03\x04payload", "application/zip")},
        )

    assert response.status_code == 500
    assert observed_path is not None and not observed_path.exists()


def test_feature_flag_blocks_mutations_but_allows_recovery_reads() -> None:
    with (
        patch(
            "backend.portfolio.api.tiantian_import_service.preview_tiantian_import",
            new_callable=AsyncMock,
        ) as preview,
        patch(
            "backend.portfolio.api.tiantian_import_service.get_tiantian_import",
            new=AsyncMock(return_value={"batchId": "batch-1", "status": "DRAFT"}),
        ),
    ):
        client = _client(enabled=False)
        blocked = client.post(
            "/api/v1/portfolio/imports/tiantian/preview",
            headers={"Idempotency-Key": "disabled"},
            data={"accountName": "天天基金账户"},
            files={"file": ("snapshot.zip", b"PK\x03\x04payload", "application/zip")},
        )
        recovered = client.get("/api/v1/portfolio/imports/batch-1")

    assert blocked.status_code == 503
    assert blocked.json()["error"] == {
        "code": "DATA_UNAVAILABLE",
        "message": "天天基金导入功能当前未启用",
        "retryable": False,
        "fieldErrors": [],
    }
    preview.assert_not_awaited()
    assert recovered.status_code == 200
    assert recovered.json()["data"]["batchId"] == "batch-1"


def test_viewer_cannot_preview_import() -> None:
    response = _client(role="VIEWER").post(
        "/api/v1/portfolio/imports/tiantian/preview",
        headers={"Idempotency-Key": "viewer"},
        data={"accountName": "天天基金账户"},
        files={"file": ("snapshot.zip", b"PK\x03\x04payload", "application/zip")},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_list_imports_forwards_bounded_cursor_contract() -> None:
    listed = AsyncMock(return_value={"items": [], "nextCursor": None})
    with patch(
        "backend.portfolio.api.tiantian_import_service.list_tiantian_imports",
        new=listed,
    ):
        response = _client(enabled=False).get(
            "/api/v1/portfolio/imports",
            params={
                "sourceType": "TIANTIAN_FUND_ZIP",
                "cursor": "2026-08-28T10:00:00+08:00",
                "limit": 25,
            },
        )

    assert response.status_code == 200
    assert response.json()["data"] == {"items": [], "nextCursor": None}
    assert listed.await_args.kwargs["source_type"] == "TIANTIAN_FUND_ZIP"
    assert listed.await_args.kwargs["limit"] == 25


def test_confirm_dispatches_tiantian_command_with_if_match_and_decisions() -> None:
    confirmed = AsyncMock(
        return_value={"batchId": "batch-1", "status": "CONFIRMED", "version": 2}
    )
    with patch(
        "backend.portfolio.api.tiantian_import_service.confirm_tiantian_import",
        new=confirmed,
    ):
        response = _client().post(
            "/api/v1/portfolio/imports/batch-1/confirm",
            headers={"Idempotency-Key": "confirm-1", "If-Match": '"1"'},
            json={
                "decisions": [{"itemId": "item-1", "startedOn": "2024-01-02"}],
                "confirmEmpty": False,
            },
        )

    assert response.status_code == 200
    kwargs = confirmed.await_args.kwargs
    assert kwargs["expected_version"] == 1
    assert kwargs["decisions"] == {"item-1": portfolio_api.date(2024, 1, 2)}
    assert kwargs["confirm_empty"] is False
    assert kwargs["idempotency_key"] == "confirm-1"


def test_confirm_maps_stale_preview_to_version_conflict() -> None:
    with patch(
        "backend.portfolio.api.tiantian_import_service.confirm_tiantian_import",
        new=AsyncMock(side_effect=VersionConflictError("预览版本已经变化")),
    ):
        response = _client().post(
            "/api/v1/portfolio/imports/batch-1/confirm",
            headers={"Idempotency-Key": "confirm-stale", "If-Match": "1"},
            json={"decisions": [], "confirmEmpty": False},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"


def test_cancel_and_metrics_retry_require_versioned_idempotent_commands() -> None:
    cancelled = AsyncMock(return_value={"batchId": "batch-1", "status": "CANCELLED", "version": 2})
    retried = AsyncMock(return_value={"batchId": "batch-2", "metricsStatus": "PENDING"})
    with (
        patch(
            "backend.portfolio.api.tiantian_import_service.cancel_tiantian_import",
            new=cancelled,
        ),
        patch(
            "backend.portfolio.api.tiantian_import_service.retry_tiantian_metrics",
            new=retried,
        ),
    ):
        client = _client()
        cancel_response = client.post(
            "/api/v1/portfolio/imports/batch-1/cancel",
            headers={"Idempotency-Key": "cancel-1", "If-Match": '"1"'},
        )
        retry_response = client.post(
            "/api/v1/portfolio/imports/batch-2/metrics/retry",
            headers={"Idempotency-Key": "retry-1"},
        )

    assert cancel_response.status_code == 200
    assert cancelled.await_args.kwargs["expected_version"] == 1
    assert retry_response.status_code == 202
    assert retried.await_args.kwargs["idempotency_key"] == "retry-1"


def test_manual_import_confirm_remains_backward_compatible_without_if_match() -> None:
    manual_confirm = AsyncMock(
        return_value={"batchId": "manual-1", "status": "CONFIRMED"}
    )
    with patch(
        "backend.portfolio.api.service.confirm_position_import",
        new=manual_confirm,
    ):
        response = _client().post(
            "/api/v1/portfolio/imports/manual-1/confirm",
            headers={"Idempotency-Key": "manual-confirm"},
        )

    assert response.status_code == 200
    manual_confirm.assert_awaited_once()

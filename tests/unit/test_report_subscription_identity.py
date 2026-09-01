"""Identity contracts for user-controlled report push subscriptions."""

import json
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from backend.errors import ValidationError_
from backend.identity.api import ReportSubscriptionsUpdateRequest
from backend.identity.domain_service import IdentityService, IdentitySettings, TenantContext


class SubscriptionConn:
    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        self.profile = profile
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        return self.profile

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, args))
        return []

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        return "INSERT 0 1"


def service() -> IdentityService:
    return IdentityService(
        object(),  # type: ignore[arg-type]
        IdentitySettings(
            jwt_secret="jwt-test-secret",
            refresh_token_hmac_key="refresh-test-secret",
            binding_hmac_key="binding-test-secret",
        ),
    )


def profile_row(**subscriptions: bool | None) -> dict[str, Any]:
    return {
        "id": "user-1",
        "email": "viewer@example.test",
        "nickname": "Viewer",
        "avatar_url": None,
        "created_at": "2026-09-01T00:00:00Z",
        "default_tenant_id": "tenant-1",
        "default_asset_scope": "MINE",
        "platform_roles": [],
        **subscriptions,
    }


@pytest.mark.asyncio
async def test_get_me_maps_report_subscriptions_and_defaults_missing_values_true() -> None:
    conn = SubscriptionConn(
        profile_row(
            pre_market_report_enabled=True,
            intraday_report_enabled=False,
            fund_1430_report_enabled=None,
            post_market_report_enabled=False,
        )
    )
    actor = TenantContext("tenant-1", "user-1", tenant_role="VIEWER")

    result = await service().get_me(conn, actor)  # type: ignore[arg-type]

    assert result["reportSubscriptions"] == {
        "PRE_MARKET": True,
        "INTRADAY": False,
        "FUND_1430": True,
        "POST_MARKET": False,
    }


@pytest.mark.asyncio
async def test_viewer_can_update_own_report_subscription_and_audit_full_state() -> None:
    conn = SubscriptionConn(
        profile_row(
            pre_market_report_enabled=True,
            intraday_report_enabled=False,
            fund_1430_report_enabled=True,
            post_market_report_enabled=True,
        )
    )
    actor = TenantContext("tenant-1", "user-1", tenant_role="VIEWER")

    result = await service().update_report_subscriptions(
        conn,  # type: ignore[arg-type]
        actor,
        {"INTRADAY": False},
    )

    assert result["INTRADAY"] is False
    update_sql, update_args = next(
        call for call in conn.calls if "INSERT INTO identity.user_preferences" in call[0]
    )
    assert "intraday_report_enabled" in update_sql
    assert update_args == ("user-1", False)
    audit_sql, audit_args = next(
        call for call in conn.calls if "INSERT INTO audit.audit_log" in call[0]
    )
    assert "audit.audit_log" in audit_sql
    assert audit_args[3] == "REPORT_SUBSCRIPTIONS_UPDATED"
    details = json.loads(audit_args[7])
    assert details == result
    assert "user_id" not in details


@pytest.mark.asyncio
async def test_empty_subscription_update_is_rejected_before_database_access() -> None:
    conn = SubscriptionConn()
    actor = TenantContext("tenant-1", "user-1", tenant_role="VIEWER")

    with pytest.raises(ValidationError_, match="至少提交一个研报订阅开关"):
        await service().update_report_subscriptions(
            conn,  # type: ignore[arg-type]
            actor,
            {},
        )

    assert conn.calls == []


def test_report_subscription_request_requires_a_known_nonempty_partial_update() -> None:
    assert ReportSubscriptionsUpdateRequest(INTRADAY=False).model_dump(
        exclude_unset=True
    ) == {"INTRADAY": False}

    with pytest.raises(PydanticValidationError):
        ReportSubscriptionsUpdateRequest()
    with pytest.raises(PydanticValidationError):
        ReportSubscriptionsUpdateRequest.model_validate({"UNKNOWN": True})
    with pytest.raises(PydanticValidationError):
        ReportSubscriptionsUpdateRequest.model_validate({"PRE_MARKET": None})

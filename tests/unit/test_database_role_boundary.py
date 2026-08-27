"""API and Worker login credentials must not share SET ROLE capabilities."""

from __future__ import annotations

import pytest

from backend.common.database import verify_runtime_login_boundary


class _Pool:
    def __init__(self, row):
        self.row = row

    def acquire(self):
        row = self.row

        class _Acquire:
            async def __aenter__(self):
                class _Conn:
                    async def fetchrow(self, _sql):
                        return row

                return _Conn()

            async def __aexit__(self, *_args):
                return None

        return _Acquire()


@pytest.mark.asyncio
async def test_api_login_rejects_worker_set_role_capability() -> None:
    with pytest.raises(RuntimeError, match="worker"):
        await verify_runtime_login_boundary(
            _Pool(
                {
                    "session_user": "api-login",
                    "current_user": "api-login",
                    "session_bypassrls": False,
                    "can_set_api": True,
                    "can_set_worker": True,
                }
            ),
            process="api",
        )


@pytest.mark.asyncio
async def test_dedicated_logins_pass_their_respective_boundaries() -> None:
    await verify_runtime_login_boundary(
        _Pool(
            {
                "session_user": "api-login",
                "current_user": "api-login",
                "session_bypassrls": False,
                "can_set_api": True,
                "can_set_worker": False,
            }
        ),
        process="api",
    )
    await verify_runtime_login_boundary(
        _Pool(
            {
                "session_user": "worker-login",
                "current_user": "wealthsail_worker",
                "session_bypassrls": False,
                # The trusted Worker deliberately downgrades to the API role
                # for per-tenant report transactions.
                "can_set_api": True,
                "can_set_worker": True,
            }
        ),
        process="worker",
    )


@pytest.mark.asyncio
async def test_worker_login_requires_api_downgrade_for_tenant_transactions() -> None:
    with pytest.raises(RuntimeError, match="wealthsail_api"):
        await verify_runtime_login_boundary(
            _Pool(
                {
                    "session_user": "worker-login",
                    "current_user": "wealthsail_worker",
                    "session_bypassrls": False,
                    "can_set_api": False,
                    "can_set_worker": True,
                }
            ),
            process="worker",
        )


@pytest.mark.asyncio
async def test_skip_does_not_accept_an_unknown_process_type() -> None:
    """A development-only skip must not bypass argument validation."""
    with pytest.raises(ValueError, match="api or worker"):
        await verify_runtime_login_boundary(_Pool(None), process="typo", skip=True)

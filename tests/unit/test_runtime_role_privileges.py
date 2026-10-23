"""Runtime startup rejects unsafe group attributes and unexpected authority."""

from unittest.mock import AsyncMock

import pytest

from backend.common.database import verify_runtime_login_boundary


class Pool:
    def __init__(self, row):
        self.row = row
        self.fetchrow = AsyncMock(return_value=row)

    def acquire(self):
        pool = self

        class Acquire:
            async def __aenter__(self):
                return pool

            async def __aexit__(self, *_args):
                return None

        return Acquire()


def _row(process, unsafe):
    return {
        "session_user": f"wealthsail_{process}_login",
        "current_user": "wealthsail_worker"
        if process == "worker"
        else "wealthsail_api_login",
        "session_superuser": False,
        "session_bypassrls": False,
        "can_set_api": True,
        "can_set_worker": process == "worker",
        "has_unsafe_runtime_role": unsafe,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("process", ["api", "worker"])
async def test_startup_rejects_unsafe_runtime_group_or_inherited_privilege(process):
    with pytest.raises(RuntimeError, match="unsafe.*role|role.*unsafe"):
        await verify_runtime_login_boundary(Pool(_row(process, True)), process=process)


@pytest.mark.asyncio
@pytest.mark.parametrize("process", ["api", "worker"])
async def test_runtime_readback_checks_group_attributes_and_set_usage_paths(process):
    pool = Pool(_row(process, False))
    await verify_runtime_login_boundary(pool, process=process)
    sql = pool.fetchrow.await_args.args[0]
    assert "has_unsafe_runtime_role" in sql
    assert "USAGE" in sql and "SET" in sql
    assert "pg_auth_members" in sql
    assert "rolcreatedb" in sql and "rolcreaterole" in sql and "rolreplication" in sql

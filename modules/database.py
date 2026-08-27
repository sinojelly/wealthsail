"""领域层数据库辅助：连接协议与 RLS 事务上下文（modules 自包含）。

与 backend.common.database 同构：所有租户作用域操作在 ``wealthsail_api``
角色 + RLS 事务内执行，行级隔离由数据库策略强制；Worker/只读引导使用
``system_connection``。测试可注入实现本文件协议的假连接/假池。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Protocol

# 与 backend.common.database.RLS_ROLE 保持一致
RLS_ROLE = "wealthsail_api"


class AsyncConnection(Protocol):
    """asyncpg.Connection 的最小可用子集（便于单元测试替身）。"""

    async def fetch(self, query: str, *args: Any) -> list[Any]: ...

    async def fetchrow(self, query: str, *args: Any) -> Any | None: ...

    async def fetchval(self, query: str, *args: Any) -> Any: ...

    async def execute(self, query: str, *args: Any) -> str: ...

    async def executemany(self, query: str, args: list[tuple]) -> None: ...

    def transaction(self) -> Any: ...


class AsyncPool(Protocol):
    """asyncpg.Pool 的最小可用子集。"""

    def acquire(self) -> Any: ...


def _claims_json(user_id: str, tenant_id: str) -> str:
    """与 backend.common.database._claims_json 同构的 JWT claims 注入串。"""
    return (
        '{"sub":"%s","role":"authenticated","tenant_id":"%s"}'
        % (user_id, tenant_id)
    )


@asynccontextmanager
async def tenant_transaction(
    pool: AsyncPool,
    user_id: str,
    tenant_id: str,
) -> AsyncIterator[AsyncConnection]:
    """租户 RLS 事务：SET LOCAL 角色 + 注入 JWT claims。

    ``SET LOCAL`` 将角色切换与 claims 限定在当次事务内，池化连接不会残留
    跨租户或提权状态（与 backend.common.database.tenant_transaction 一致）。
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {RLS_ROLE};")
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true);",
                _claims_json(user_id, tenant_id),
            )
            yield conn


@asynccontextmanager
async def api_system_transaction(
    pool: AsyncPool,
) -> AsyncIterator[AsyncConnection]:
    """API 角色系统事务：身份表与 privileged RPC 均要求 wealthsail_api。

    事务型池化部署（Supabase Supavisor / PgBouncer）会在客户端间复用后端
    连接而不重置会话级 SET ROLE，因此显式切换角色是信任边界的一部分。
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL ROLE {RLS_ROLE};")
            yield conn


@asynccontextmanager
async def system_connection(
    pool: AsyncPool,
) -> AsyncIterator[AsyncConnection]:
    """无角色切换的连接（Worker 角色或只读引导查询）。"""
    async with pool.acquire() as conn:
        yield conn

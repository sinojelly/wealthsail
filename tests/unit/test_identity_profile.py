"""Unit tests for identity profile update / avatar validation (no DB / FS writes)."""

from __future__ import annotations

import pytest

from backend.common.tenancy import TenantContext
from backend.errors import ValidationError_
from backend.identity.service import (
    AVATAR_MAX_BYTES,
    save_avatar,
    update_profile,
)

CTX = TenantContext(user_id="user-1", tenant_id="tenant-1", tenant_role="OWNER")


class FakeConn:
    """模拟 asyncpg.Connection：记录 execute，fetchrow 返回预设行。"""

    def __init__(self, row=None) -> None:
        self.row = row
        self.executed: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query, args))
        return self.row

    async def execute(self, query: str, *args):
        self.executed.append((query, args))
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_update_profile_rejects_empty_body() -> None:
    with pytest.raises(ValidationError_):
        await update_profile(FakeConn(), CTX)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_update_profile_rejects_blank_nickname() -> None:
    with pytest.raises(ValidationError_):
        await update_profile(FakeConn(), CTX, nickname="   ")


@pytest.mark.asyncio
async def test_update_profile_nickname_only() -> None:
    conn = FakeConn(
        row={
            "id": "user-1",
            "email": "zhangming@example.com",
            "nickname": "张明同学",
            "avatar_url": None,
        }
    )
    result = await update_profile(conn, CTX, nickname="张明同学")  # type: ignore[arg-type]
    assert result["nickname"] == "张明同学"
    query, args = conn.fetchrow_calls[0]
    assert "nickname = $1" in query
    assert "avatar_url = $" not in query
    assert args[-1] == "user-1"
    # 审计写入
    assert any("audit.audit_log" in q for q, _ in conn.executed)


@pytest.mark.asyncio
async def test_update_profile_avatar_url_only() -> None:
    conn = FakeConn(
        row={
            "id": "user-1",
            "email": "zhangming@example.com",
            "nickname": "张明",
            "avatar_url": "/static/avatars/x.jpg",
        }
    )
    result = await update_profile(conn, CTX, avatar_url="/static/avatars/x.jpg")  # type: ignore[arg-type]
    assert result["avatarUrl"] == "/static/avatars/x.jpg"
    query, _ = conn.fetchrow_calls[0]
    assert "avatar_url = $1" in query
    assert "nickname = $" not in query


# ---------------------------------------------------------------------------
# save_avatar 校验（不触盘：只测拒绝分支）
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_save_avatar_rejects_bad_extension() -> None:
    with pytest.raises(ValidationError_):
        await save_avatar(  # type: ignore[arg-type]
            None, CTX, filename="evil.exe", content_type="image/png", data=b"x"
        )


@pytest.mark.asyncio
async def test_save_avatar_rejects_non_image_content_type() -> None:
    with pytest.raises(ValidationError_):
        await save_avatar(  # type: ignore[arg-type]
            None, CTX, filename="photo.png", content_type="text/plain", data=b"x"
        )


@pytest.mark.asyncio
async def test_save_avatar_rejects_empty_and_oversize() -> None:
    with pytest.raises(ValidationError_):
        await save_avatar(  # type: ignore[arg-type]
            None, CTX, filename="photo.png", content_type="image/png", data=b""
        )
    with pytest.raises(ValidationError_):
        await save_avatar(  # type: ignore[arg-type]
            None,
            CTX,
            filename="photo.png",
            content_type="image/png",
            data=b"x" * (AVATAR_MAX_BYTES + 1),
        )

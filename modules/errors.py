"""领域层共享异常（modules 自包含，不依赖 backend.*）。

所有业务校验失败、授权失败与数据缺失都以显式异常表达，由 HTTP 适配层
（backend）映射为对应状态码；领域服务内部只抛出这些异常，不触碰传输层。
"""

from __future__ import annotations


class WealthSailError(Exception):
    """所有领域异常的共同基类（可按类目单独捕获）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(WealthSailError):
    """输入校验失败（参数缺失、格式错误、业务规则不满足）。"""


class AuthRequiredError(WealthSailError):
    """未认证或凭据无效（账号不存在与口令错误统一为同一消息，防账号枚举）。"""


class ForbiddenError(WealthSailError):
    """已认证但角色/租户成员关系不允许该操作。"""


class NotFoundError(WealthSailError):
    """目标资源不存在、已停用或对当前用户不可见。"""


class ConflictError(WealthSailError):
    """幂等键/唯一约束冲突或状态不允许（重复注册、重复提交等）。"""


class DataUnavailableError(WealthSailError):
    """数据源不可用或投影尚未生成（行情抓取失败、无快照数据）。"""

"""Bounded ZIP parser for local Tiantian Fund import packages."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile, ZipInfo

from pydantic import ValidationError

from backend.errors import ValidationError_
from backend.portfolio.imports.tiantian import (
    ExcludedSummary,
    TiantianManifest,
    TiantianPackage,
    TiantianPosition,
    TiantianTransaction,
)

MANIFEST_NAME = "manifest.json"
DATA_MEMBERS = frozenset(
    {
        "current-positions.json",
        "fund-transactions.json",
        "review-required.json",
        "excluded-summary.json",
    }
)
ALLOWED_MEMBERS = DATA_MEMBERS | {MANIFEST_NAME}


@dataclass(frozen=True)
class ImportLimits:
    max_compressed_bytes: int = 20 * 1024 * 1024
    max_expanded_bytes: int = 100 * 1024 * 1024
    max_entries: int = 20
    max_compression_ratio: int = 100
    max_positions: int = 1000
    max_transactions: int = 20000
    max_json_depth: int = 32
    max_text_length: int = 2000


def _invalid(message: str, field: str = "file") -> ValidationError_:
    return ValidationError_(
        message,
        field_errors=[{"field": field, "message": message}],
    )


def _safe_name(info: ZipInfo) -> str:
    normalized = info.filename.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or normalized.startswith("/"):
        raise _invalid(f"ZIP 包含不安全路径：{info.filename}")
    if len(path.parts) != 1:
        raise _invalid(f"ZIP 只允许根目录文件：{info.filename}")
    return normalized


def _validate_info(info: ZipInfo, limits: ImportLimits) -> str:
    name = _safe_name(info)
    if info.is_dir():
        raise _invalid(f"ZIP 不允许目录项：{name}")
    if info.flag_bits & 0x1:
        raise _invalid(f"ZIP 不允许加密文件：{name}")
    unix_mode = info.external_attr >> 16
    if unix_mode and stat.S_ISLNK(unix_mode):
        raise _invalid(f"ZIP 不允许符号链接：{name}")
    if info.file_size > 0:
        if info.compress_size <= 0:
            raise _invalid(f"ZIP 文件压缩信息无效：{name}")
        if info.file_size / info.compress_size > limits.max_compression_ratio:
            raise _invalid(f"ZIP 文件压缩率超过限制：{name}")
    return name


def _decode_json(content: bytes, name: str) -> Any:
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _invalid(f"{name} 不是合法 UTF-8", name) from exc
    try:
        return json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise _invalid(f"{name} 不是合法 JSON", name) from exc


def _validate_json_shape(
    value: Any,
    limits: ImportLimits,
    name: str,
    *,
    depth: int = 0,
) -> None:
    if depth > limits.max_json_depth:
        raise _invalid(f"{name} JSON 嵌套超过限制", name)
    if isinstance(value, str):
        if len(value) > limits.max_text_length:
            raise _invalid(f"{name} 文本字段超过限制", name)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_shape(item, limits, name, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if len(str(key)) > limits.max_text_length:
                raise _invalid(f"{name} 文本字段超过限制", name)
            _validate_json_shape(item, limits, name, depth=depth + 1)


def _validate_models(model, payload: Any, name: str):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise _invalid(
            f"{name} 字段校验失败：{first.get('msg', 'invalid value')}",
            f"{name}.{location}" if location else name,
        ) from exc


def _read_members(path: Path, limits: ImportLimits) -> dict[str, bytes]:
    if not path.is_file():
        raise _invalid("导入 ZIP 不存在")
    if path.stat().st_size > limits.max_compressed_bytes:
        raise _invalid("ZIP 压缩后大小超过限制")
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_entries:
                raise _invalid("ZIP 文件数量超过限制")
            members: dict[str, bytes] = {}
            expanded = 0
            for info in infos:
                name = _validate_info(info, limits)
                if name in members:
                    raise _invalid(f"ZIP 包含重复文件：{name}")
                if name not in ALLOWED_MEMBERS:
                    raise _invalid(f"ZIP 包含未声明文件：{name}")
                expanded += info.file_size
                if expanded > limits.max_expanded_bytes:
                    raise _invalid("ZIP 解压后大小超过限制")
                members[name] = archive.read(info)
    except BadZipFile as exc:
        raise _invalid("文件不是合法 ZIP") from exc
    if set(members) != ALLOWED_MEMBERS:
        missing = ", ".join(sorted(ALLOWED_MEMBERS - set(members)))
        raise _invalid(f"ZIP 缺少必需文件：{missing}")
    return members


def parse_tiantian_package(
    path: Path,
    limits: ImportLimits | None = None,
) -> TiantianPackage:
    """Parse a v2 package without trusting names, sizes, hashes, or JSON types."""
    limits = limits or ImportLimits()
    members = _read_members(path, limits)
    manifest_payload = _decode_json(members[MANIFEST_NAME], MANIFEST_NAME)
    _validate_json_shape(manifest_payload, limits, MANIFEST_NAME)
    manifest = _validate_models(TiantianManifest, manifest_payload, MANIFEST_NAME)
    manifest_files = {item.name: item for item in manifest.files}
    if len(manifest_files) != len(manifest.files) or set(manifest_files) != DATA_MEMBERS:
        raise _invalid("Manifest 文件清单必须与 v2 数据文件完全一致", MANIFEST_NAME)

    payloads: dict[str, Any] = {}
    for name in DATA_MEMBERS:
        content = members[name]
        if hashlib.sha256(content).hexdigest() != manifest_files[name].sha256:
            raise _invalid(f"文件哈希不匹配：{name}", name)
        payload = _decode_json(content, name)
        _validate_json_shape(payload, limits, name)
        actual_records = len(payload) if isinstance(payload, (list, dict)) else -1
        if actual_records != manifest_files[name].records:
            raise _invalid(f"文件记录数与 Manifest 不一致：{name}", name)
        payloads[name] = payload

    positions_payload = payloads["current-positions.json"]
    transactions_payload = payloads["fund-transactions.json"]
    review_payload = payloads["review-required.json"]
    if not isinstance(positions_payload, list) or len(positions_payload) > limits.max_positions:
        raise _invalid("当前持仓记录数量超过限制", "current-positions.json")
    if not isinstance(transactions_payload, list) or len(transactions_payload) > limits.max_transactions:
        raise _invalid("基金流水记录数量超过限制", "fund-transactions.json")
    if not isinstance(review_payload, list) or len(review_payload) > limits.max_transactions:
        raise _invalid("待复核记录数量超过限制", "review-required.json")

    positions = tuple(
        _validate_models(TiantianPosition, item, f"current-positions.json[{index}]")
        for index, item in enumerate(positions_payload)
    )
    transactions = tuple(
        _validate_models(TiantianTransaction, item, f"fund-transactions.json[{index}]")
        for index, item in enumerate(transactions_payload)
    )
    excluded = _validate_models(
        ExcludedSummary,
        payloads["excluded-summary.json"],
        "excluded-summary.json",
    )
    if not all(isinstance(item, dict) for item in review_payload):
        raise _invalid("待复核记录必须是对象数组", "review-required.json")
    if (
        manifest.counts.current_positions != len(positions)
        or manifest.counts.fund_transactions != len(transactions)
        or manifest.counts.review_required != len(review_payload)
        or manifest.counts.cash_management_excluded != excluded.cash_management
        or manifest.counts.non_posting_excluded != excluded.non_posting
    ):
        raise _invalid("Manifest 汇总数量与数据文件不一致", MANIFEST_NAME)

    return TiantianPackage(
        manifest=manifest,
        positions=positions,
        transactions=transactions,
        review_required=tuple(review_payload),
        excluded_summary=excluded,
    )

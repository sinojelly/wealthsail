"""Behavior tests for the bounded Tiantian Fund v2 package parser."""

from __future__ import annotations

import hashlib
import json
import warnings
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from backend.errors import ValidationError_
from backend.portfolio.imports.package import ImportLimits, parse_tiantian_package


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _record_count(content: bytes) -> int:
    payload = json.loads(content)
    return len(payload) if isinstance(payload, list) else 1


def _package_payloads() -> dict[str, bytes]:
    return {
        "current-positions.json": _json_bytes(
            [
                {
                    "sourceProvider": "TIANTIAN_FUND",
                    "sourceRecordId": "holding-110022",
                    "productCode": "110022",
                    "productName": "易方达消费行业股票",
                    "quantity": "1234.56",
                    "availableQuantity": "1200.00",
                    "averageCost": "2.02501296",
                    "costAmount": "2500.00",
                    "marketValue": "2700.00",
                    "holdingProfit": "200.00",
                    "holdingProfitRate": "8.00",
                    "latestNav": "2.1876",
                    "latestNavDate": "2026-08-27",
                    "accountMask": "****5678",
                    "capturedAt": "2026-08-28T00:00:00+08:00",
                }
            ]
        ),
        "fund-transactions.json": _json_bytes(
            [
                {
                    "sourceProvider": "TIANTIAN_FUND",
                    "sourceId": "tx-1",
                    "traceNo": "trace-1",
                    "occurredAt": "2026-08-20 12:00:00",
                    "productCode": "110022",
                    "productName": "易方达消费行业股票",
                    "businessCode": "022",
                    "businessType": "买入",
                    "normalizedBusinessType": "PURCHASE",
                    "applicationValue": "2500.00",
                    "applicationUnit": "元",
                    "confirmationValue": "1234.56",
                    "confirmationUnit": "份",
                    "accountMask": "****5678",
                    "status": "成功",
                    "category": "FUND_TRADE",
                    "classificationReason": "FUND_PRODUCT_CODE",
                    "detailRef": "tx-1:022:trace-1",
                    "sourceWindowStart": "2020-01-01",
                    "sourceWindowEnd": "2026-08-28",
                    "sourcePage": 1,
                }
            ]
        ),
        "review-required.json": _json_bytes([]),
        "excluded-summary.json": _json_bytes(
            {
                "cashManagement": 1545,
                "nonPosting": 4,
                "duplicateCount": 0,
                "completenessReasons": [],
            }
        ),
    }


def write_synthetic_package(
    directory: Path,
    *,
    payloads: dict[str, bytes] | None = None,
    manifest_overrides: dict | None = None,
    extra_entries: dict[str, bytes] | None = None,
) -> Path:
    payloads = payloads or _package_payloads()
    files = [
        {
            "name": name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "records": _record_count(content),
        }
        for name, content in payloads.items()
    ]
    manifest = {
        "schemaVersion": "wealthsail-tiantian-import/v2",
        "collectorVersion": "0.2.0",
        "provider": "TIANTIAN_FUND",
        "externalAccountKey": "1f8a8407-9d91-4a1d-b954-9ad46fbd9585",
        "sourceWindowStart": "2020-01-01",
        "sourceWindowEnd": "2026-08-28",
        "positionsAsOf": "2026-08-28T00:00:00+08:00",
        "generatedAt": "2026-08-28T00:01:00+08:00",
        "completeness": "COMPLETE",
        "counts": {
            "currentPositions": 1,
            "fundTransactions": 1,
            "reviewRequired": 0,
            "cashManagementExcluded": 1545,
            "nonPostingExcluded": 4,
        },
        "files": files,
        "security": {
            "credentialsStored": False,
            "accessTokenStored": False,
            "accountNumbersMasked": True,
        },
    }
    manifest.update(manifest_overrides or {})
    path = directory / "wealthsail-tiantian-20260828.zip"
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _json_bytes(manifest))
        for name, content in payloads.items():
            archive.writestr(name, content)
        for name, content in (extra_entries or {}).items():
            archive.writestr(name, content)
    return path


def test_parser_returns_decimal_source_models_for_a_valid_v2_package(tmp_path: Path) -> None:
    package_path = write_synthetic_package(tmp_path)

    parsed = parse_tiantian_package(package_path, ImportLimits())

    assert parsed.manifest.schema_version == "wealthsail-tiantian-import/v2"
    assert parsed.positions[0].product_code == "110022"
    assert parsed.positions[0].quantity == Decimal("1234.56")
    assert parsed.positions[0].average_cost == Decimal("2.02501296")
    assert parsed.transactions[0].normalized_business_type == "PURCHASE"
    assert parsed.excluded_summary.cash_management == 1545


def test_parser_rejects_a_payload_whose_bytes_do_not_match_manifest_hash(
    tmp_path: Path,
) -> None:
    payloads = _package_payloads()
    files = [
        {
            "name": name,
            "sha256": (
                "0" * 64
                if name == "current-positions.json"
                else hashlib.sha256(content).hexdigest()
            ),
            "records": _record_count(content),
        }
        for name, content in payloads.items()
    ]
    package_path = write_synthetic_package(
        tmp_path,
        payloads=payloads,
        manifest_overrides={"files": files},
    )

    with pytest.raises(ValidationError_, match="文件哈希不匹配"):
        parse_tiantian_package(package_path, ImportLimits())


def test_parser_rejects_duplicate_member_names(tmp_path: Path) -> None:
    package_path = write_synthetic_package(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(package_path, "a", ZIP_DEFLATED) as archive:
            archive.writestr("current-positions.json", _json_bytes([]))

    with pytest.raises(ValidationError_, match="重复文件"):
        parse_tiantian_package(package_path, ImportLimits())


def test_parser_rejects_parent_path_entries_before_reading_json(tmp_path: Path) -> None:
    package_path = write_synthetic_package(
        tmp_path,
        extra_entries={"../outside.json": b"{}"},
    )

    with pytest.raises(ValidationError_, match="不安全路径"):
        parse_tiantian_package(package_path, ImportLimits())


def test_parser_enforces_compressed_upload_limit(tmp_path: Path) -> None:
    package_path = write_synthetic_package(tmp_path)

    with pytest.raises(ValidationError_, match="压缩后大小超过限制"):
        parse_tiantian_package(package_path, ImportLimits(max_compressed_bytes=1))


def test_parser_rejects_json_nesting_deeper_than_configured_limit(tmp_path: Path) -> None:
    nested: object = "leaf"
    for _ in range(40):
        nested = {"child": nested}
    payloads = _package_payloads()
    payloads["review-required.json"] = _json_bytes([nested])
    package_path = write_synthetic_package(
        tmp_path,
        payloads=payloads,
        manifest_overrides={
            "counts": {
                "currentPositions": 1,
                "fundTransactions": 1,
                "reviewRequired": 1,
                "cashManagementExcluded": 1545,
                "nonPostingExcluded": 4,
            }
        },
    )

    with pytest.raises(ValidationError_, match="JSON 嵌套超过限制"):
        parse_tiantian_package(package_path, ImportLimits())


def test_parser_rejects_source_text_longer_than_configured_limit(tmp_path: Path) -> None:
    payloads = _package_payloads()
    payloads["review-required.json"] = _json_bytes([{"detail": "x" * 2001}])
    package_path = write_synthetic_package(
        tmp_path,
        payloads=payloads,
        manifest_overrides={
            "counts": {
                "currentPositions": 1,
                "fundTransactions": 1,
                "reviewRequired": 1,
                "cashManagementExcluded": 1545,
                "nonPostingExcluded": 4,
            }
        },
    )

    with pytest.raises(ValidationError_, match="文本字段超过限制"):
        parse_tiantian_package(package_path, ImportLimits())


@pytest.mark.parametrize(
    ("member_name", "record_index"),
    [
        ("current-positions.json", 0),
        ("fund-transactions.json", 0),
    ],
)
def test_parser_rejects_unmasked_account_numbers_even_when_manifest_claims_masking(
    tmp_path: Path,
    member_name: str,
    record_index: int,
) -> None:
    payloads = _package_payloads()
    records = json.loads(payloads[member_name])
    records[record_index]["accountMask"] = "6222021234565678"
    payloads[member_name] = _json_bytes(records)
    package_path = write_synthetic_package(tmp_path, payloads=payloads)

    with pytest.raises(ValidationError_, match="账号必须为空或仅保留末四位"):
        parse_tiantian_package(package_path, ImportLimits())


@pytest.mark.parametrize(
    ("member_name", "identity_field", "manifest_count"),
    [
        ("current-positions.json", "productCode", "currentPositions"),
        ("fund-transactions.json", "sourceId", "fundTransactions"),
    ],
)
def test_parser_rejects_duplicate_business_identities(
    tmp_path: Path,
    member_name: str,
    identity_field: str,
    manifest_count: str,
) -> None:
    payloads = _package_payloads()
    records = json.loads(payloads[member_name])
    duplicate = dict(records[0])
    duplicate["sourceRecordId" if member_name == "current-positions.json" else "traceNo"] = (
        "second-source-row"
    )
    assert duplicate[identity_field] == records[0][identity_field]
    records.append(duplicate)
    payloads[member_name] = _json_bytes(records)
    counts = {
        "currentPositions": 1,
        "fundTransactions": 1,
        "reviewRequired": 0,
        "cashManagementExcluded": 1545,
        "nonPostingExcluded": 4,
    }
    counts[manifest_count] = 2
    package_path = write_synthetic_package(
        tmp_path,
        payloads=payloads,
        manifest_overrides={"counts": counts},
    )

    with pytest.raises(ValidationError_, match="业务主键重复"):
        parse_tiantian_package(package_path, ImportLimits())


@pytest.mark.parametrize("field_name", ["applicationValue", "confirmationValue"])
def test_parser_rejects_negative_transaction_values(
    tmp_path: Path,
    field_name: str,
) -> None:
    payloads = _package_payloads()
    records = json.loads(payloads["fund-transactions.json"])
    records[0][field_name] = "-1"
    payloads["fund-transactions.json"] = _json_bytes(records)
    package_path = write_synthetic_package(tmp_path, payloads=payloads)

    with pytest.raises(ValidationError_, match="交易申请或确认值不能为负数"):
        parse_tiantian_package(package_path, ImportLimits())

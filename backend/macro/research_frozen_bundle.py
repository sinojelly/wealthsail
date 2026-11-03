"""Decode bounded frozen evidence without changing its identity or evidence clock.

A file export proves self-consistency only. API callers must separately verify
the server-owned run, archived state, initial receipt and tenant permissions.
"""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from uuid import UUID

MATERIAL_LIMITS = {
    "PACKET": 4 * 1024**2,
    "RAW_MODEL": 2 * 1024**2,
    "SOURCE_CHECKS": 512 * 1024,
}
MAX_EXPORT_BYTES = 48 * 1024**2


class FrozenBundleError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class FrozenResearch:
    research_id: str
    validated_at: datetime
    packet: dict
    raw: str
    source_checks: dict
    material_hashes: dict

    def repair_payload(self):
        return {
            "researchId": self.research_id,
            "validatedAt": self.validated_at.isoformat(),
            "packet": self.packet,
            "raw": self.raw,
            "sourceChecks": self.source_checks,
        }


def _stamp(value):
    try:
        stamp = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
        return stamp
    except (TypeError, ValueError, AttributeError):
        raise FrozenBundleError("MATERIAL_CLOCK_INVALID") from None


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise FrozenBundleError("MATERIAL_JSON_INVALID")
        value[key] = item
    return value


def _constant(_value):
    raise FrozenBundleError("MATERIAL_JSON_INVALID")


def _depth(value, level=0):
    if level > 32:
        raise FrozenBundleError("MATERIAL_JSON_INVALID")
    if isinstance(value, dict):
        for nested in value.values():
            _depth(nested, level + 1)
    elif isinstance(value, list):
        for nested in value:
            _depth(nested, level + 1)


def _object(text):
    try:
        value = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
        if not isinstance(value, dict):
            raise ValueError
        _depth(value)
        return value
    except (ValueError, TypeError, RecursionError):
        raise FrozenBundleError("MATERIAL_JSON_INVALID") from None


def decode_frozen_bundle(bundle, expected_run_id, *, observed_at):
    # NAS read-only exports have a named-artifact envelope. Translate only that
    # closed shape; integrityVerified is descriptive, never a bypass of hashes.
    if isinstance(bundle, dict) and "artifacts" in bundle:
        artifacts = bundle["artifacts"]
        names = {
            "packet": "PACKET",
            "raw_output": "RAW_MODEL",
            "source_checks": "SOURCE_CHECKS",
        }
        if (
            "items" in bundle
            or "complete" in bundle
            or not isinstance(artifacts, dict)
            or set(artifacts) != set(names)
            or any(not isinstance(row, dict) for row in artifacts.values())
        ):
            raise FrozenBundleError("MATERIALS_INCOMPLETE")
        bundle = {
            "researchId": bundle.get("researchId"),
            "validatedAt": bundle.get("validatedAt"),
            "complete": True,
            "items": [
                {**artifacts[name], "kind": kind} for name, kind in names.items()
            ],
        }
    try:
        identity = str(UUID(str(bundle["researchId"])))
        if identity != str(UUID(str(expected_run_id))):
            raise ValueError
    except (KeyError, ValueError, TypeError):
        raise FrozenBundleError("MATERIAL_IDENTITY_INVALID") from None
    stamp, observed = _stamp(bundle.get("validatedAt")), _stamp(observed_at)
    if stamp > observed:
        raise FrozenBundleError("MATERIAL_CLOCK_INVALID")
    items = bundle.get("items")
    if (
        bundle.get("complete") is not True
        or not isinstance(items, list)
        or len(items) != 3
    ):
        raise FrozenBundleError("MATERIALS_INCOMPLETE")
    texts, hashes = {}, {}
    for item in items:
        if not isinstance(item, dict):
            raise FrozenBundleError("MATERIALS_INCOMPLETE")
        kind, content, digest, size = (
            item.get(key) for key in ("kind", "content", "sha256", "byteLength")
        )
        if (
            not isinstance(kind, str)
            or kind not in MATERIAL_LIMITS
            or kind in texts
            or item.get("omissionReason") is not None
            or not isinstance(content, str)
        ):
            raise FrozenBundleError("MATERIALS_INCOMPLETE")
        try:
            data = content.encode("utf-8")
        except UnicodeError:
            raise FrozenBundleError("MATERIAL_ENCODING_INVALID") from None
        if (
            len(data) > MATERIAL_LIMITS[kind]
            or type(size) is not int
            or size != len(data)
        ):
            raise FrozenBundleError("MATERIAL_SIZE_INVALID")
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or sha256(data).hexdigest() != digest
        ):
            raise FrozenBundleError("MATERIAL_HASH_MISMATCH")
        texts[kind], hashes[kind] = content, digest
    packet, raw, checks = (_object(texts[kind]) for kind in MATERIAL_LIMITS)
    if _stamp(packet.get("asOf")) > stamp:
        raise FrozenBundleError("MATERIAL_CLOCK_INVALID")
    for check in checks.values():
        if not isinstance(check, dict):
            raise FrozenBundleError("MATERIAL_JSON_INVALID")
        if check.get("verification") == "SOURCE_CHECKED" and not check.get(
            "retrievedAt"
        ):
            raise FrozenBundleError("MATERIAL_CLOCK_INVALID")
        for field in ("retrievedAt", "availableAt", "publishedAt"):
            if check.get(field) is not None and _stamp(check[field]) > stamp:
                raise FrozenBundleError("MATERIAL_CLOCK_INVALID")
    return FrozenResearch(identity, stamp, packet, texts["RAW_MODEL"], checks, hashes)


def read_frozen_bundle(path, expected_run_id, *, observed_at):
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(MAX_EXPORT_BYTES + 1)
    except OSError:
        raise FrozenBundleError("EXPORT_UNAVAILABLE") from None
    if len(data) > MAX_EXPORT_BYTES:
        raise FrozenBundleError("EXPORT_SIZE_LIMIT")
    try:
        bundle = _object(data.decode("utf-8-sig"))
    except UnicodeError:
        raise FrozenBundleError("MATERIAL_ENCODING_INVALID") from None
    return decode_frozen_bundle(bundle, expected_run_id, observed_at=observed_at)

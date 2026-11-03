"""Frozen evidence decoding is bounded and cannot rewrite its research clock."""

from copy import deepcopy
from datetime import timedelta
import hashlib
import json

import pytest

from test_daily_research import NOW
from test_industrial_metals_research_regression import case

RUN = "ceaa7c1c-d01f-4afa-b2b3-43d77ef04409"


def wrap(kind, value):
    content = (
        value
        if isinstance(value, str)
        else json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )
    encoded = content.encode("utf-8")
    return {
        "kind": kind,
        "content": content,
        "byteLength": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "omissionReason": None,
    }


def export_bundle():
    # Synthetic material identity for decoder tests; this is not the NAS export.
    packet, raw, checks = case()
    return {
        "researchId": RUN,
        "validatedAt": NOW.isoformat(),
        "complete": True,
        "items": [
            wrap("PACKET", packet),
            wrap("RAW_MODEL", raw),
            wrap("SOURCE_CHECKS", checks),
        ],
    }


def decode(bundle):
    from backend.macro.research_frozen_bundle import decode_frozen_bundle

    return decode_frozen_bundle(bundle, RUN, observed_at=NOW + timedelta(days=3))


def test_decodes_original_bytes_clock_hashes_and_does_not_mutate_export():
    original = export_bundle()
    before = deepcopy(original)
    frozen = decode(original)
    assert frozen.research_id == RUN
    assert frozen.validated_at == NOW
    assert frozen.raw == original["items"][1]["content"]
    assert frozen.material_hashes == {
        row["kind"]: row["sha256"] for row in original["items"]
    }
    assert frozen.packet["asOf"] == NOW.isoformat()
    assert original == before


def test_nas_export_envelope_is_normalized_without_trusting_integrity_flag():
    original = export_bundle()
    names = {
        "PACKET": "packet",
        "RAW_MODEL": "raw_output",
        "SOURCE_CHECKS": "source_checks",
    }
    exported = {
        "researchId": RUN,
        "validatedAt": original["validatedAt"],
        "integrityVerified": True,
        "artifacts": {
            names[row["kind"]]: {
                key: row[key] for key in ("content", "sha256", "byteLength")
            }
            for row in original["items"]
        },
    }
    before = deepcopy(exported)
    assert decode(exported) == decode(original)
    assert exported == before
    exported["artifacts"]["packet"]["content"] += " "
    with pytest.raises(ValueError, match="MATERIAL_SIZE_INVALID"):
        decode(exported)


@pytest.mark.parametrize(
    "change",
    [
        "id",
        "missing",
        "duplicate",
        "omission",
        "hash",
        "bytes",
        "bool_bytes",
        "future",
        "naive",
        "incomplete",
    ],
)
def test_refuses_wrong_identity_missing_or_tampered_materials(change):
    original = export_bundle()
    if change == "id":
        original["researchId"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    elif change == "missing":
        original["items"].pop()
    elif change == "duplicate":
        original["items"][2] = deepcopy(original["items"][0])
    elif change == "omission":
        original["items"][1]["omissionReason"] = "SIZE_LIMIT"
    elif change == "hash":
        original["items"][0]["sha256"] = "0" * 64
    elif change == "bytes":
        original["items"][0]["byteLength"] += 1
    elif change == "bool_bytes":
        original["items"][0]["byteLength"] = True
    elif change == "future":
        original["validatedAt"] = (NOW + timedelta(days=10)).isoformat()
    elif change == "naive":
        original["validatedAt"] = NOW.replace(tzinfo=None).isoformat()
    else:
        original["complete"] = False
    with pytest.raises(ValueError):
        decode(original)


@pytest.mark.parametrize("kind", ["PACKET", "RAW_MODEL", "SOURCE_CHECKS"])
def test_refuses_actual_utf8_size_over_limit_even_with_matching_hash(kind):
    from backend.macro.research_frozen_bundle import MATERIAL_LIMITS

    original = export_bundle()
    original["items"] = [
        wrap(kind, "中" * (MATERIAL_LIMITS[kind] // 3 + 1))
        if row["kind"] == kind
        else row
        for row in original["items"]
    ]
    with pytest.raises(ValueError):
        decode(original)


def test_duplicate_json_keys_and_clock_mismatch_cannot_be_hidden_by_valid_hash():
    for kind, text in (
        ("PACKET", '{"asOf":"x","asOf":"y"}'),
        ("RAW_MODEL", '{"sources":[],"sources":[]}'),
    ):
        original = export_bundle()
        original["items"] = [
            wrap(kind, text) if row["kind"] == kind else row
            for row in original["items"]
        ]
        with pytest.raises(ValueError):
            decode(original)
    packet, raw, checks = case()
    packet["asOf"] = (NOW + timedelta(minutes=1)).isoformat()
    original = export_bundle()
    original["items"][0] = wrap("PACKET", packet)
    with pytest.raises(ValueError):
        decode(original)
    checks["s1"]["retrievedAt"] = (NOW + timedelta(seconds=1)).isoformat()
    original = export_bundle()
    original["items"][2] = wrap("SOURCE_CHECKS", checks)
    with pytest.raises(ValueError):
        decode(original)

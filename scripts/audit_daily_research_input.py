"""Offline, content-safe model input audit; no network, AI or database access.

Prints field names/counts/bytes only, never frozen text, URLs, prompts or outputs.
Run with an explicit trusted local Git baseline to reproduce an old build_prompt.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.macro import daily_research  # noqa: E402


def encoded(value, *, compact=False):
    options = {"separators": (",", ":")} if compact else {}
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False, **options
    ).encode("utf-8")


def load_frozen_packet(path):
    frozen = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    rows = [row for row in frozen.get("items", []) if row.get("kind") == "PACKET"]
    if len(rows) != 1 or not isinstance(rows[0].get("content"), str):
        raise ValueError("one complete frozen packet is required")
    row = rows[0]
    content = row["content"].encode("utf-8")
    if row.get("omissionReason") or row.get("byteLength") != len(content):
        raise ValueError("frozen packet is incomplete")
    if hashlib.sha256(content).hexdigest() != row.get("sha256"):
        raise ValueError("frozen packet digest mismatch")
    return json.loads(content)


def legacy_prompt(packet, baseline_ref):
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", baseline_ref + "^{commit}"], text=True
    ).strip()
    projection = subprocess.check_output(
        ["git", "show", commit + ":backend/macro/model_projection.py"]
    ).decode("utf-8")
    old_model = {"__name__": "_wealthsail_local_audit_projection"}
    exec(compile(projection, "<trusted local baseline projection>", "exec"), old_model)
    source = subprocess.check_output(
        ["git", "show", commit + ":backend/macro/daily_research.py"]
    ).decode("utf-8")
    module = types.ModuleType("_wealthsail_local_audit_daily")
    sys.modules[module.__name__] = module
    try:
        exec(
            compile(source, "<trusted local baseline research>", "exec"),
            module.__dict__,
        )
        module.model_packet = old_model["model_packet"]
        return (
            module.build_prompt(packet),
            commit,
            module.REPORT_SCHEMA,
            old_model["model_packet"](packet),
        )
    finally:
        sys.modules.pop(module.__name__, None)


def repeated_strings(value):
    strings = Counter()

    def walk(item):
        if isinstance(item, dict):
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str) and len(item.encode("utf-8")) >= 64:
            strings[item] += 1

    walk(value)
    return {
        "minimumUtf8Bytes": 64,
        "repeatedValues": sum(count > 1 for count in strings.values()),
        "extraOccurrences": sum(count - 1 for count in strings.values() if count > 1),
        "extraLiteralUtf8Bytes": sum(
            len(text.encode("utf-8")) * (count - 1)
            for text, count in strings.items()
            if count > 1
        ),
    }


def field_sizes(view, *, compact):
    rows = []

    def add(path, value):
        rows.append(
            {
                "path": path,
                "utf8Bytes": len(encoded(value, compact=compact)),
                "elements": len(value) if isinstance(value, (dict, list)) else 1,
            }
        )

    for key, value in view.items():
        add(key, value)
        if isinstance(value, dict):
            for child, nested in value.items():
                add(key + "." + child, nested)
        elif isinstance(value, list) and all(isinstance(row, dict) for row in value):
            for child in sorted({field for row in value for field in row}):
                add(key + "[*]." + child, [row[child] for row in value if child in row])
    return sorted(rows, key=lambda row: (-row["utf8Bytes"], row["path"]))


def exact_evidence_duplicates(view):
    rows = [
        *view.get("documents", []),
        *view.get("candidateIndex", []),
        *(view.get("alphaPublic") or {}).get("catalysts", []),
    ]
    for group in (view.get("institutionalSignals") or {}).values():
        if isinstance(group, list):
            rows.extend(row for row in group if isinstance(row, dict))
    facts = Counter()
    for row in rows:
        value = next(
            (
                row.get(key)
                for key in ("content", "summary", "quote")
                if isinstance(row.get(key), str)
            ),
            None,
        )
        if value:
            facts[value] += 1
    return {
        "textBearingRows": sum(facts.values()),
        "distinctExactTexts": len(facts),
        "extraExactTextOccurrences": sum(count - 1 for count in facts.values()),
    }


def expand_text_references(view):
    texts = view.get("sharedText") or {}

    def expand(value):
        if isinstance(value, dict):
            if set(value) == {"textRef"}:
                return texts[value["textRef"]]
            return {
                key: expand(child)
                for key, child in value.items()
                if key != "sharedText"
            }
        if isinstance(value, list):
            return [expand(child) for child in value]
        return value

    return expand(view)


def prompt_summary(prompt, *, compact):
    serialized = prompt.rsplit("\n", 1)[-1]
    view = json.loads(serialized)
    fields = field_sizes(view, compact=compact)
    return {
        "promptUtf8Bytes": len(prompt.encode("utf-8")),
        "projectionUtf8Bytes": len(serialized.encode("utf-8")),
        "instructionUtf8Bytes": len(prompt.encode("utf-8"))
        - len(serialized.encode("utf-8")),
        "topLevel": [row for row in fields if "." not in row["path"]][:20],
        "top20FieldsIncludingChildren": fields[:20],
        "repeatedStrings": repeated_strings(view),
        "physicalExactEvidenceTextDuplicates": exact_evidence_duplicates(view),
        "logicalExactEvidenceTextDuplicates": exact_evidence_duplicates(
            expand_text_references(view)
        ),
        "sharedTextValues": len(view.get("sharedText") or {}),
    }


def audit(packet, baseline_ref):
    before = encoded(packet)
    old, commit, old_schema, old_direct_projection = legacy_prompt(packet, baseline_ref)
    new = daily_research.build_prompt(packet)
    if encoded(packet) != before:
        raise AssertionError("frozen packet was mutated")
    previous = prompt_summary(old, compact=False)
    current = prompt_summary(new, compact=True)
    return {
        "version": "daily-model-input-audit-v1",
        "baselineCommit": commit,
        "modelProjectionVersion": "daily-model-input-v2",
        "oldReportSchemaUtf8Bytes": len(encoded(old_schema)),
        "newReportSchemaUtf8Bytes": len(encoded(daily_research.REPORT_SCHEMA)),
        "oldDirectModelPacketUtf8Bytes": len(encoded(old_direct_projection)),
        "schemaChanged": old_schema != daily_research.REPORT_SCHEMA,
        "originalPacketUnchanged": True,
        "old": previous,
        "new": current,
        "promptReductionPct": round(
            100 * (1 - current["promptUtf8Bytes"] / previous["promptUtf8Bytes"]), 2
        ),
        "notes": [
            "Bytes are not tokens or model runtime.",
            "Parent and child sizes overlap and must not be summed.",
            "Only identical literal text is shared; same URL never proves identical fact.",
            "All macro observation points and news text remain available through references.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", required=True)
    parser.add_argument("--baseline-ref", required=True)
    args = parser.parse_args()
    try:
        result = audit(load_frozen_packet(args.frozen), args.baseline_ref)
    except Exception as error:
        # No exception message: malformed documents may embed private content.
        print(
            json.dumps({"status": "AUDIT_FAILED", "errorClass": type(error).__name__})
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only event associations, separate from industry or investment eligibility."""

from copy import deepcopy
import re

from pydantic import ValidationError

LINK_FIELDS = (
    "relatedTopicKeys",
    "relatedFormalDirectionKeys",
    "relatedMacroConcepts",
    "candidateConceptLabels",
)
RESOLUTION_VERSION = "research-event-links-v1"
NAMESPACES = {"TOPIC", "FORMAL_DIRECTION", "LEGACY_DIRECTION"}
METHODS = {"EXACT", "CANONICAL"}
REASONS = {"UNKNOWN_REFERENCE", "AMBIGUOUS_REFERENCE"}


def _strings(value):
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def canonical_key(value):
    """Only ASCII key spelling is normalized; never infer from labels or sectors."""
    return (
        value.lower().replace("_", "-")
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,120}", value)
        else None
    )


def resolve_reference(value, registered_keys):
    """Return (key, method, reason); an exact unique key precedes alias matching."""
    keys = [key for key in registered_keys if isinstance(key, str)]
    exact = keys.count(value)
    if exact == 1:
        return value, "EXACT", None
    if exact > 1:
        return None, None, "AMBIGUOUS_REFERENCE"
    canonical = canonical_key(value)
    matching = [
        key for key in keys if canonical is not None and canonical_key(key) == canonical
    ]
    if len(matching) == 1:
        return matching[0], "CANONICAL", None
    return None, None, "AMBIGUOUS_REFERENCE" if matching else "UNKNOWN_REFERENCE"


def valid_topic_keys(catalog, source_aliases):
    """Register structural/source-valid observations, including unverified sources."""
    from backend.macro.research_topics import TopicProposal

    result = []
    for row in catalog if isinstance(catalog, list) else []:
        try:
            topic = TopicProposal.model_validate(row)
        except ValidationError:
            continue
        if all(ref in source_aliases for ref in topic.sourceIds):
            result.append(topic.key)
    return result


def resolved_formal_dependencies(row, formal_keys):
    """Let repair retain real formal dependencies even before a candidate is removed."""
    return {
        key
        for value in [
            *_strings(row.get("affectedDirectionIds")),
            *_strings(row.get("relatedFormalDirectionKeys")),
        ]
        for key, _, _ in [resolve_reference(value, formal_keys)]
        if key is not None
    }


def possible_formal_dependencies(row, formal_keys):
    """Conservative risk closure only; never resolve a link or grant eligibility.

    Pruning a candidate must not turn an originally ambiguous spelling into a
    unique reference to a different retained direction.
    """
    keys = [key for key in formal_keys if isinstance(key, str)]
    dependencies = resolved_formal_dependencies(row, keys)
    for value in [
        *_strings(row.get("affectedDirectionIds")),
        *_strings(row.get("relatedFormalDirectionKeys")),
    ]:
        _, _, reason = resolve_reference(value, keys)
        if reason == "AMBIGUOUS_REFERENCE":
            exact = [key for key in keys if key == value]
            canonical = canonical_key(value)
            dependencies.update(
                exact
                or [
                    key
                    for key in keys
                    if canonical is not None and canonical_key(key) == canonical
                ]
            )
    return dependencies


def normalize_event_links(
    row, *, packet, formal_keys, source_aliases, topic_catalog=None
):
    from backend.macro.research_topics import is_v2

    result = deepcopy(row)
    for field in LINK_FIELDS:
        result[field] = list(dict.fromkeys(_strings(row.get(field))))
    topics = valid_topic_keys(topic_catalog, source_aliases)
    resolution = {"version": RESOLUTION_VERSION, "resolved": [], "unresolved": []}

    def resolve(value, namespace, keys):
        key, method, reason = resolve_reference(value, keys)
        if key is not None:
            resolution["resolved"].append(
                {
                    "namespace": namespace,
                    "inputKey": value,
                    "key": key,
                    "method": method,
                }
            )
        else:
            resolution["unresolved"].append(
                {"namespace": namespace, "key": value, "reasonCode": reason}
            )
        return key

    for field, namespace, keys in (
        ("relatedTopicKeys", "TOPIC", topics),
        ("relatedFormalDirectionKeys", "FORMAL_DIRECTION", formal_keys),
    ):
        result[field] = list(
            dict.fromkeys(
                key
                for value in _strings(row.get(field))
                if (key := resolve(value, namespace, keys)) is not None
            )
        )

    if is_v2(packet):
        legacy = []
        seed_keys = [
            seed["id"]
            for seed in packet.get("directions", [])
            if isinstance(seed, dict) and isinstance(seed.get("id"), str)
        ]
        for value in _strings(row.get("affectedDirectionIds")):
            formal, _, formal_reason = resolve_reference(value, formal_keys)
            if formal is not None:
                resolve(value, "FORMAL_DIRECTION", formal_keys)
                legacy.append(formal)
                result["relatedFormalDirectionKeys"].append(formal)
            elif value in seed_keys:
                resolve(value, "LEGACY_DIRECTION", seed_keys)
                legacy.append(value)
            else:
                topic, _, topic_reason = resolve_reference(value, topics)
                if topic is not None:
                    resolve(value, "TOPIC", topics)
                    result["relatedTopicKeys"].append(topic)
                else:
                    resolution["unresolved"].append(
                        {
                            "namespace": "LEGACY_DIRECTION",
                            "key": value,
                            "reasonCode": "AMBIGUOUS_REFERENCE"
                            if "AMBIGUOUS_REFERENCE" in (formal_reason, topic_reason)
                            else "UNKNOWN_REFERENCE",
                        }
                    )
                    result["candidateConceptLabels"].append(value)
        result["affectedDirectionIds"] = list(dict.fromkeys(legacy))
    for field in LINK_FIELDS:
        result[field] = list(dict.fromkeys(result[field]))
    result["referenceResolution"] = merge_reference_resolution(resolution)
    return result


def safe_reference_resolution(value):
    """Closed primitive DTO; model-authored extra fields or proof methods never pass."""
    if not isinstance(value, dict) or value.get("version") != RESOLUTION_VERSION:
        return None
    result = {"version": RESOLUTION_VERSION, "resolved": [], "unresolved": []}
    for field, allowed, discriminator in (
        ("resolved", METHODS, "method"),
        ("unresolved", REASONS, "reasonCode"),
    ):
        rows = value.get(field)
        for row in rows[:576] if isinstance(rows, list) else []:
            fields = ("inputKey", "key") if field == "resolved" else ("key",)
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("namespace"), str)
                or row["namespace"] not in NAMESPACES
                or not isinstance(row.get(discriminator), str)
                or row[discriminator] not in allowed
                or any(
                    not isinstance(row.get(key), str) or not 1 <= len(row[key]) <= 2000
                    for key in fields
                )
            ):
                continue
            result[field].append(
                {
                    "namespace": row["namespace"],
                    **{key: row[key] for key in fields},
                    discriminator: row[discriminator],
                }
            )
    return result


def merge_reference_resolution(*values):
    result = {"version": RESOLUTION_VERSION, "resolved": [], "unresolved": []}
    for value in values:
        safe = safe_reference_resolution(value)
        if safe is not None:
            for field in ("resolved", "unresolved"):
                for row in safe[field]:
                    if row not in result[field]:
                        result[field].append(row)
    return result


def safe_event_links(row):
    result = {
        field: [text[:2000] for text in _strings(row.get(field))[:384]]
        for field in LINK_FIELDS
        if field in row
    }
    resolution = safe_reference_resolution(row.get("referenceResolution"))
    if resolution is not None:
        result["referenceResolution"] = resolution
    return result

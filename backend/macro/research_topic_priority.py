"""Bounded read priority; this projection never changes topic or investment status."""

from backend.errors import ValidationError_

VERSION = "topic-display-priority-v1"
LIMIT = 10
IMPORTANCE = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
PROGRESS = {"QUARANTINED": 0, "PENDING_REVIEW": 1, "OBSERVATION_ONLY": 2, "FORMAL": 3}


def topic_highlights(items, *, accepted=None, limit=LIMIT):
    """Use only safe server projections and explicit links, never title inference.

    HIGH importance remains first even with unverified or adverse evidence.
    Evidence coverage describes quote checking, not the truth of the inference.
    Unknown fields remain unknown. Original order is the final stable tie-break.
    """
    if not isinstance(items, list) or len(items) > 72:
        raise ValidationError_("主题重点投影超过完整目录边界")
    if type(limit) is not int or not 1 <= limit <= 72:
        raise ValidationError_("主题重点显示数量无效")
    accepted = accepted if isinstance(accepted, dict) else {}
    importance = {}
    for component in ("events", "upcoming"):
        rows = accepted.get(component)
        for row in rows[:12] if isinstance(rows, list) else []:
            if not isinstance(row, dict) or row.get("importance") not in IMPORTANCE:
                continue
            keys = row.get("relatedTopicKeys")
            for key in keys[:384] if isinstance(keys, list) else []:
                if isinstance(key, str):
                    importance[key] = max(
                        importance.get(key, 0), IMPORTANCE[row["importance"]]
                    )
    ranked = []
    for ordinal, topic in enumerate(items):
        importance_value = importance.get(topic["key"], 0)
        level = next(
            key for key, value in IMPORTANCE.items() if value == importance_value
        )
        score = topic.get("evidenceCoverageScore")
        score = score if type(score) is int and 0 <= score <= 100 else None
        verified = topic.get("match", {}).get("scopeVerified") is True
        progress = topic["status"]
        reasons = []
        if importance_value:
            reasons.append(f"EXPLICIT_{level}_IMPORTANCE")
        if score is not None and score > 0:
            reasons.append("CHECKED_SOURCE_SUPPORT")
        if verified:
            reasons.append("SCOPE_LINK_VERIFIED")
        reasons.append("RESEARCH_PROGRESS_AND_ORIGINAL_ORDER")
        ranked.append(
            (
                (
                    -importance_value,
                    -(score if score is not None else -1),
                    -int(verified),
                    -PROGRESS.get(progress, 0),
                    ordinal,
                ),
                {
                    "topic": topic,
                    "priority": {
                        "importance": level,
                        "checkedSourceCoveragePercent": score,
                        "scopeVerified": verified,
                        "researchProgress": progress,
                        "reasons": reasons,
                    },
                },
            )
        )
    ranked.sort(key=lambda value: value[0])
    return {
        "version": VERSION,
        "limit": limit,
        "catalogCount": len(items),
        "items": [row for _, row in ranked[:limit]],
        "actionable": False,
    }

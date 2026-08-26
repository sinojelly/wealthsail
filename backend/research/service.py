"""Read model for evidence- and policy-gated recommendations."""

from __future__ import annotations

from backend.dependencies import now_shanghai
from backend.research import repository

DISCLAIMER = (
    "仅展示已持久化且通过有效期、证据和风险政策门禁的投研候选；"
    "需要用户独立判断与确认，不构成自动交易指令。"
)

_ACTION_LABELS = {
    "HOLD": "持有候选",
    "OBSERVE": "观察",
    "REVIEW": "复核",
    "REDUCE_CANDIDATE": "减仓候选",
    "ADD_CANDIDATE": "加仓候选",
    "ABSTAIN": "暂不建议",
}


async def get_latest_recommendations(conn, tenant_id: str) -> dict:
    """Build a deterministic view; model/provider calls are forbidden here."""
    rows = await repository.latest_publishable_recommendations(
        conn, tenant_id, limit=4
    )
    generated_at = now_shanghai()
    if not rows:
        return {
            "status": "ABSTAIN",
            "reasonCode": "NO_GOVERNED_RECOMMENDATION",
            "qualityStatus": "UNAVAILABLE",
            "items": [],
            "disclaimer": DISCLAIMER,
            "generatedAt": generated_at,
            "model": None,
        }

    qualities = [str(row["evidence_quality"]) for row in rows]
    quality = (
        "STALE"
        if "STALE" in qualities
        else "ESTIMATED"
        if "ESTIMATED" in qualities
        else "FRESH"
    )
    items = [
        {
            "id": str(row["id"]),
            "action": _ACTION_LABELS.get(row["recommendation_type"], "复核"),
            "title": (
                f"{row['canonical_name']}（{row['ticker']}）"
                if row["canonical_name"]
                else "组合级投研候选"
            ),
            "detail": row["summary"],
            "counterCase": row["counter_case"],
            "confidence": (
                str(row["confidence"]) if row["confidence"] is not None else None
            ),
            "riskLevel": (
                "中" if row["policy_check_status"] == "WARNING" else "低"
            ),
            "policyCheckStatus": row["policy_check_status"],
            "evidenceCount": int(row["evidence_count"]),
            "evidenceQuality": row["evidence_quality"],
            "validUntil": row["valid_until"],
            "methodVersion": row["prompt_template_version"],
        }
        for row in rows
    ]
    return {
        "status": "PUBLISHED",
        "reasonCode": None,
        "qualityStatus": quality,
        "items": items,
        "disclaimer": DISCLAIMER,
        "generatedAt": generated_at,
        "model": rows[0]["model"],
    }

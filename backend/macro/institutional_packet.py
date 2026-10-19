"""Public institutional clues without claiming ownership from order flows."""

from copy import deepcopy
from backend.integrations.institutional_signals import (
    extract_media_institutional_signals,
)


def institutional_packet(catalysts, etf_result, now):
    from backend.macro.daily_research import _pick, _stamp, _url

    media = extract_media_institutional_signals(catalysts, now)
    flows = []
    for row in etf_result.get("signals", [])[:40]:
        try:
            stamp = _stamp(row.get("retrievedAt"))
            if (
                stamp is None
                or stamp > now
                or row.get("metric")
                not in {"ETF_SHARE_CHANGE", "SECONDARY_MAIN_NET_FLOW_CNY"}
            ):
                continue
            fields = (
                "id",
                "fundCode",
                "fundName",
                "metric",
                "value",
                "unit",
                "referenceDate",
                "comparisonDate",
                "retrievedAt",
                "observedAt",
                "verification",
                "definition",
                "previousShares",
                "currentShares",
                "comparisonSourceUri",
            )
            item = {key: deepcopy(row[key]) for key in fields if key in row}
            item["sourceUri"] = _url(row.get("sourceUri"))
            item["institutionAttribution"] = "UNKNOWN"
            flows.append(item)
        except (ValueError, TypeError):
            continue
    return {
        "researchViews": [row for row in media if row["kind"] == "RESEARCH_VIEW"][:12],
        "corporateActions": [
            row
            for row in media
            if row["kind"] in {"CORPORATE_BUYBACK", "EARNINGS_UPGRADE"}
        ][:20],
        "etfMediaFlows": [row for row in media if row["kind"] == "ETF_MEDIA_FLOW"][:6],
        "etfFlows": flows,
        "sourceResults": {
            key: _pick(
                row, ("status", "records", "errorClass", "coverage", "limitations")
            )
            for key, row in etf_result.get("sourceResults", {}).items()
            if isinstance(row, dict)
        },
        "limitations": [
            "研报是机构观点；公司回购及业绩按实际披露阶段研究。",
            "ETF份额净变化和二级主力资金流分别统计，均不证明机构投资者身份或机构现金净申购。",
        ],
    }

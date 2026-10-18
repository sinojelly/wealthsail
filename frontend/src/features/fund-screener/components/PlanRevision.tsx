import type { ActionableZoneResult } from "../../../api/investment";
import {
  completeTrack,
  entryDistance,
  planPrice,
  revisionLabels,
} from "../plan-display";
import { readingText } from "../research-reading";

export default function PlanRevision({ plan }: { plan: ActionableZoneResult }) {
  const selected = plan.selectedTrack ?? plan.track;
  const other =
    selected &&
    plan.tracks.find(
      (track) => track.track !== selected && completeTrack(track),
    );
  const distance = other ? entryDistance(plan.currentPrice, other) : null;
  const price = (value: number | string | null) => planPrice(value, plan);
  const note = readingText(plan.priceRevisionNote ?? "", Infinity);
  const extended =
    Array.from(note).length > 180 ||
    (note.match(/\b\d+\.\d{3,}\b/g)?.length ?? 0) >= 4;
  return (
    <>
      {(plan.priceRevisionStatus || plan.priceRevisionNote) && (
        <p
          className="op-value-revision"
          role="status"
          aria-label="价格计划调整"
        >
          {plan.priceRevisionStatus &&
            `计划调整：${revisionLabels[plan.priceRevisionStatus]}。`}
          {!extended && note}
        </p>
      )}
      {extended && (
        <details className="op-revision-details op-print-disclosure">
          <summary>查看完整调整原因</summary>
          <p>{note}</p>
        </details>
      )}
      {other && (
        <p className="op-alternate-track">
          备选{other.track === "LEFT_VALUE" ? "左" : "右"}轨参考：入场{" "}
          {price(other.entryZoneMin)}–{price(other.entryZoneMax)} | 止损{" "}
          {price(other.stopLossPrice)} | 目标 {price(other.takeProfitT1)} /{" "}
          {price(other.takeProfitT2)}
          {distance === null
            ? "（距离待核对）"
            : distance === 0
              ? "（已进入参考区）"
              : `（距买区 ${distance.toFixed(2)}%）`}
          {other.state === "WATCH" && <small>该轨技术条件仍待触发。</small>}
        </p>
      )}
    </>
  );
}

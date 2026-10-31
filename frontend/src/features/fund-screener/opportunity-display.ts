import type {
  ActionableZoneResult,
  ActionableZoneTrack,
  EntrySafety,
  OpportunityPriority,
  QuantitativeScanItem,
} from "../../api/investment";
import {
  completeTrack,
  entryAllowed,
  entryDistance,
  planEntryStatus,
} from "./plan-display";

type EntryGuard = EntrySafety & {
  triggerState?: string;
  inEntryZone?: boolean;
  subscriptionRestricted?: boolean;
  preferredEntryEligible?: boolean;
};
const number = (value: unknown) =>
  value != null && value !== "" && Number.isFinite(Number(value))
    ? Number(value)
    : null;
const industryRiskFlags = new Set([
  "FINANCING_COOLING",
  "LAYOFFS_CLOSURES",
  "ORDER_CUTS",
  "OVERCAPACITY_PRICE_WAR",
  "PRODUCTION_DELAY",
  "DEBUBBLING",
  "EARLY_COMMERCIALIZATION",
  "NO_EARNINGS_DELIVERY",
  "FORWARD_PLANS_ONLY",
  "NO_CURRENT_EARNINGS_PROOF",
  "PERSISTED_RISK_WATCH",
]);

function hasIndustryRisk(
  plan: ActionableZoneResult,
  track?: ActionableZoneTrack,
) {
  const penalty = number(
    track?.scoreBreakdown?.industryCyclePenalty ??
      plan.scoreBreakdown?.industryCyclePenalty,
  );
  return (
    plan.industryCycle?.stage === "EARLY_COMMERCIALIZATION" ||
    (plan.industryCycle?.riskLevel != null &&
      plan.industryCycle.riskLevel !== "NONE") ||
    !!plan.industryCycle?.riskFlags?.some((flag) =>
      industryRiskFlags.has(flag),
    ) ||
    !!plan.industryRiskWarning?.trim() ||
    (penalty != null && penalty < 0)
  );
}
export function highPriceZone(
  plan: ActionableZoneResult,
  track?: ActionableZoneTrack,
) {
  const rsi = number(plan.indicators.rsi14),
    current = number(plan.currentPrice),
    target = number(track?.takeProfitT1);
  return (
    (rsi != null && rsi >= 70) ||
    (current != null &&
      current > 0 &&
      target != null &&
      target > 0 &&
      current >= target)
  );
}
export function priceInZone(
  plan: ActionableZoneResult,
  track?: ActionableZoneTrack,
) {
  const current = number(plan.currentPrice),
    low = number(track?.entryZoneMin),
    high = number(track?.entryZoneMax);
  return (
    current != null &&
    current > 0 &&
    low != null &&
    high != null &&
    low > 0 &&
    low <= high &&
    current >= low &&
    current <= high
  );
}
function preferredEntryEligible(
  plan: ActionableZoneResult,
  track?: ActionableZoneTrack,
  signal?: EntryGuard,
) {
  return (
    !plan.subscriptionRestricted &&
    plan.preferredEntryEligible !== false &&
    entryAllowed(plan, signal, track)
  );
}
export function planEntryReady(
  plan: ActionableZoneResult,
  track?: ActionableZoneTrack,
  signal?: EntryGuard,
) {
  return (
    track?.state === "CANDIDATE" &&
    (signal?.triggerState == null || signal.triggerState === "CANDIDATE") &&
    signal?.inEntryZone !== false &&
    priceInZone(plan, track) &&
    !highPriceZone(plan, track) &&
    preferredEntryEligible(plan, track, signal)
  );
}
export function buyZoneStatus(
  plan: ActionableZoneResult,
  track?: ActionableZoneTrack,
  signal?: EntryGuard,
): {
  text: string;
  tone: "left" | "right" | "wait" | "away" | "high" | "rose";
  warning?: boolean;
  position?: boolean;
} {
  const sellRows = [plan, signal, track].filter(
    (row) => row?.inSellZone === true,
  );
  const sellStatus =
    sellRows.find(
      (row) => row?.entryStatusTone === "rose" && row.entryStatus,
    ) ?? sellRows[0];
  if (sellStatus)
    return {
      text:
        (sellStatus.entryStatusTone === "rose" && sellStatus.entryStatus) ||
        "🔥 已入建议卖出区间·持仓宜止盈/空仓勿追高",
      tone: "rose",
      warning: true,
    };
  // Keep the server's checked risk status before geometric and score projections.
  if (
    [plan, signal, track].some((row) => row?.entryStatus || row?.entryBlocked)
  ) {
    const protectedStatus = planEntryStatus(plan, track, signal)!;
    return {
      ...protectedStatus,
      tone: protectedStatus.warning ? "wait" : "away",
      position: !protectedStatus.warning,
    };
  }
  if (highPriceZone(plan, track))
    return { text: "🔥 已入高位止盈区（勿追高）", tone: "high" };
  if (plan.subscriptionRestricted || plan.preferredEntryEligible === false)
    return {
      text: plan.subscriptionRestricted
        ? "⚠️ 申购受限·暂缓入场"
        : "⚠️ 暂缓入场",
      tone: "wait",
      warning: true,
    };
  const position = planEntryStatus(plan, track, signal);
  if (position) return { ...position, tone: "away", position: true };
  if (!track) return { text: "价格依据待补齐", tone: "away" };
  if (planEntryReady(plan, track, signal))
    return track.track === "LEFT_VALUE"
      ? { text: "🎯 已入左侧低吸买区", tone: "left" }
      : { text: "📈 已入右侧趋势买区", tone: "right" };
  if (priceInZone(plan, track))
    return {
      text:
        track.track === "RIGHT_TREND"
          ? "价格近均线·待趋势触发"
          : "已处左侧参考区·待条件触发",
      tone: "wait",
    };
  const distance = entryDistance(plan.currentPrice, track);
  return {
    text:
      distance != null && Number.isFinite(distance)
        ? `⏳ 距${track.track === "LEFT_VALUE" ? "左侧" : "右侧"}买区 ${distance.toFixed(2)}%（待触发）`
        : "价格依据待补齐",
    tone: "away",
  };
}
export function buyZoneClassName(
  status: ReturnType<typeof buyZoneStatus>,
  ready = false,
) {
  return `op-buy-zone-status tone-${status.tone}${status.warning ? " op-entry-warning" : status.position ? " op-entry-position" : ready ? " op-scan-hit" : ""}`;
}
export function priorityScore(value?: OpportunityPriority) {
  const score = value?.opportunityScore;
  return typeof score === "number" &&
    Number.isFinite(score) &&
    score >= 0 &&
    score <= 100
    ? score
    : 0;
}
export function rewardRisk(track?: ActionableZoneTrack) {
  const saved = number(track?.rewardRiskRatio);
  if (saved != null && saved > 0) return saved;
  const low = number(track?.entryZoneMin),
    high = number(track?.entryZoneMax),
    stop = number(track?.stopLossPrice),
    target = number(track?.takeProfitT1);
  if (low == null || high == null || stop == null || target == null) return 0;
  const mid = (low + high) / 2;
  return mid > stop && target > mid ? (target - mid) / (mid - stop) : 0;
}
export function planPriority(
  plan: ActionableZoneResult,
  track?: ActionableZoneTrack,
  signal?: EntryGuard,
) {
  const ready = planEntryReady(plan, track, signal);
  return {
    group: highPriceZone(plan, track) ? 2 : ready ? 0 : 1,
    blocked: [plan, track, signal].some(
      (row: EntryGuard | undefined) =>
        row?.entryBlocked === true ||
        row?.subscriptionRestricted === true ||
        row?.preferredEntryEligible === false,
    ),
    industryRisk: hasIndustryRisk(plan, track),
    score: priorityScore(track),
    rewardRisk:
      typeof track?.opportunityScore === "number" &&
      Number.isFinite(track.opportunityScore)
        ? rewardRisk(track)
        : 0,
    distance: track
      ? (entryDistance(plan.currentPrice, track) ?? Infinity)
      : Infinity,
  };
}
export function comparePriority(
  a: ReturnType<typeof planPriority>,
  b: ReturnType<typeof planPriority>,
) {
  return (
    a.group - b.group ||
    Number(a.blocked) - Number(b.blocked) ||
    Number(a.industryRisk) - Number(b.industryRisk) ||
    b.score - a.score ||
    b.rewardRisk - a.rewardRisk ||
    a.distance - b.distance
  );
}
export function preferredScanTrack(item: QuantitativeScanItem) {
  return item.signals
    .flatMap((signal) => {
      const track = item.tracks.find(
        (row) => row.track === signal.track && completeTrack(row),
      );
      return track ? [{ signal, track }] : [];
    })
    .sort(
      (a, b) =>
        comparePriority(
          planPriority(item, a.track, a.signal),
          planPriority(item, b.track, b.signal),
        ) || a.track.track.localeCompare(b.track.track),
    )[0];
}
export function bestDirectionPriority(plans: ActionableZoneResult[]) {
  return (
    plans
      .map((plan) =>
        planPriority(
          plan,
          plan.tracks.find(
            (track) => track.track === (plan.selectedTrack ?? plan.track),
          ),
        ),
      )
      .sort(comparePriority)[0] ?? {
      group: 3,
      blocked: false,
      industryRisk: false,
      score: 0,
      rewardRisk: 0,
      distance: Infinity,
    }
  );
}

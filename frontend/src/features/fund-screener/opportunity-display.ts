import type {
  ActionableZoneResult,
  ActionableZoneTrack,
  OpportunityPriority,
  QuantitativeScanItem,
} from "../../api/investment";
import { completeTrack, entryDistance } from "./plan-display";

type EntryGuard = {
  entryBlocked?: boolean;
  entryReady?: boolean;
  inBuyZone?: boolean;
  entryStatus?: string | null;
  entryStatusTone?: string | null;
};
const number = (value: unknown) =>
  value != null && value !== "" && Number.isFinite(Number(value))
    ? Number(value)
    : null;
const guard = (value: object | undefined) => value as EntryGuard | undefined;
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
export function planEntryReady(
  plan: ActionableZoneResult,
  track?: ActionableZoneTrack,
  signal?: EntryGuard,
) {
  return (
    track?.state === "CANDIDATE" &&
    priceInZone(plan, track) &&
    !highPriceZone(plan, track) &&
    [guard(plan), guard(track), signal].every(
      (row) =>
        !row?.entryBlocked &&
        row?.entryReady !== false &&
        row?.inBuyZone !== false,
    )
  );
}
export function buyZoneStatus(
  plan: ActionableZoneResult,
  track?: ActionableZoneTrack,
  signal?: EntryGuard,
): { text: string; tone: "left" | "right" | "wait" | "away" | "high" } {
  const override = [guard(plan), guard(track), signal].find(
    (row) => row?.entryStatus,
  );
  if (override?.entryStatus)
    return {
      text: override.entryStatus,
      tone:
        override.entryBlocked || override.entryStatusTone === "amber"
          ? "wait"
          : "away",
    };
  if (highPriceZone(plan, track))
    return { text: "🔥 已入高位止盈区（勿追高）", tone: "high" };
  if ([guard(plan), guard(track), signal].some((row) => row?.entryBlocked))
    return { text: "⚠️ 暂缓入场", tone: "wait" };
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
  return {
    ready: planEntryReady(plan, track, signal),
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
    Number(b.ready) - Number(a.ready) ||
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
      ready: false,
      score: 0,
      rewardRisk: 0,
      distance: Infinity,
    }
  );
}

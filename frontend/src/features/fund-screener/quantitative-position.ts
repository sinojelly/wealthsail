import type {
  ActionableZoneResult,
  ActionableZoneTrack,
} from "../../api/investment";

export const finitePrice = (value: unknown): number | null =>
  value != null &&
  String(value).trim() !== "" &&
  Number.isFinite(Number(value)) &&
  Number(value) > 0
    ? Number(value)
    : null;

export function referenceTrack(
  plan: ActionableZoneResult,
): ActionableZoneTrack | undefined {
  return (
    plan.tracks?.find(
      (row) => row.track === (plan.selectedTrack ?? plan.track),
    ) ??
    plan.tracks?.find(
      (row) =>
        finitePrice(row.entryZoneMin) != null &&
        finitePrice(row.entryZoneMax) != null,
    ) ??
    plan.tracks?.[0]
  );
}

export function quantitativePosition(plan: ActionableZoneResult) {
  const track = referenceTrack(plan);
  const current = finitePrice(plan.currentPrice);
  const low = finitePrice(track?.entryZoneMin),
    high = finitePrice(track?.entryZoneMax);
  const sellLow = finitePrice(track?.sellZoneMin ?? track?.takeProfitT1);
  const sellHigh = finitePrice(track?.sellZoneMax ?? track?.takeProfitT2);
  const stop = finitePrice(track?.stopLossPrice);
  const distance = (min: number | null, max: number | null) =>
    current == null || min == null || max == null || min > max
      ? null
      : current < min
        ? ((min - current) / current) * 100
        : current > max
          ? ((current - max) / current) * 100
          : 0;
  const buyDistance = distance(low, high),
    sellDistance = distance(sellLow, sellHigh);
  let label = "⚪ 区间中部观察",
    tone = "neutral",
    rank = 2;
  if (
    current == null ||
    low == null ||
    high == null ||
    (plan.priceRefresh != null && plan.priceRefresh.state !== "READY")
  ) {
    label = "⚪ 净值资料补齐中";
    rank = 4;
  } else if (stop != null && current < stop) {
    label = "🔴 跌破止损参考";
    tone = "stop";
    rank = 4;
  } else if (sellLow != null && current >= sellLow) {
    label = "🟠 已入卖区（分批止盈）";
    tone = "sell";
    rank = 3;
  } else if (current <= high) {
    label = "🟢 已入买区（低吸参考）";
    tone = "buy";
    rank = 0;
  } else if (buyDistance != null && buyDistance <= 3) {
    label = `🟡 接近买区（差 ${buyDistance.toFixed(2)}%）`;
    tone = "near";
    rank = 1;
  } else if (
    plan.tracks?.some(
      (row) => row.track === "RIGHT_TREND" && row.state === "CANDIDATE",
    )
  ) {
    label = "🔵 右侧趋势走强";
    tone = "trend";
  }
  return {
    track,
    current,
    low,
    high,
    sellLow,
    sellHigh,
    stop,
    buyDistance,
    sellDistance,
    label,
    tone,
    rank,
  };
}

import type { ActionableZoneResult } from "../../api/investment";
import { finitePrice, referenceTrack } from "./quantitative-position";

const navUnits = new Set([
  "CNY",
  "元",
  "元/份",
  "CNY/unit",
  "CNY/份",
  "CNY/share",
]);

/** Display urgency only. It does not grant eligibility or alter saved plans. */
export function fundOperationDistance(
  plan?: ActionableZoneResult | null,
  currentPrice?: unknown,
) {
  const valid =
    plan?.priceBasis === "UNIT_NAV" && navUnits.has(plan.unit ?? "");
  const track = valid && plan ? referenceTrack(plan) : undefined;
  const current = valid
    ? finitePrice(currentPrice ?? plan?.currentPrice)
    : null;
  const low = finitePrice(track?.entryZoneMin),
    high = finitePrice(track?.entryZoneMax);
  const sellLow = finitePrice(track?.sellZoneMin ?? track?.takeProfitT1);
  const sellHigh = finitePrice(track?.sellZoneMax ?? track?.takeProfitT2);
  const stop = finitePrice(track?.stopLossPrice);
  const distance = (min: number | null, max: number | null) =>
    current == null || min == null || max == null || min > max
      ? null
      : Number(
          (current < min
            ? ((min - current) / current) * 100
            : current > max
              ? ((current - max) / current) * 100
              : 0
          ).toFixed(10),
        );
  const inBuyZone =
    current != null &&
    low != null &&
    high != null &&
    low <= current &&
    current <= high;
  const inSellZone =
    current != null &&
    sellLow != null &&
    sellHigh != null &&
    sellLow <= sellHigh &&
    current >= sellLow;
  const percentileValue = plan?.indicators?.navPercentile2y;
  const percentile =
    percentileValue != null && Number.isFinite(Number(percentileValue))
      ? Number(percentileValue)
      : null;
  return {
    track,
    current,
    low,
    high,
    sellLow,
    sellHigh,
    stop,
    percentile,
    buyDistance: distance(low, high),
    sellDistance: inSellZone ? 0 : distance(sellLow, sellHigh),
    inBuyZone,
    inSellZone,
    stopTriggered: current != null && stop != null && current <= stop,
  };
}

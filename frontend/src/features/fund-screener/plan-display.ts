import Decimal from "decimal.js";
import type { ActionableZoneResult, PlanPrice } from "../../api/investment";

export function planPrice(
  value: PlanPrice | undefined,
  result: Pick<ActionableZoneResult, "subjectKind" | "priceBasis">,
): string {
  if (value == null || !Number.isFinite(Number(value)) || Number(value) <= 0)
    return "—";
  return new Decimal(value).toFixed(
    result.subjectKind === "index" || result.priceBasis.startsWith("INDEX_")
      ? 2
      : result.priceBasis === "UNIT_NAV"
        ? 4
        : 3,
    Decimal.ROUND_HALF_UP,
  );
}
export function planUnit(unit: string | null): string {
  return (
    (
      {
        points: "指数点",
        index_point: "指数点",
        CNY: "元",
        "CNY/份": "元/份",
        "CNY/unit": "元/份",
        "CNY/share": "元/份",
        HKD: "港元",
        USD: "美元",
      } as Record<string, string>
    )[unit ?? ""] ??
    unit ??
    "单位待确认"
  );
}

export function planTimeBasisNote(
  result: Pick<ActionableZoneResult, "priceTimeBasis">,
): string | null {
  return result.priceTimeBasis === "LATEST_DISCLOSED_NAV"
    ? "最新披露净值参考，存在跨市场发布延迟；不是当日估值。"
    : null;
}

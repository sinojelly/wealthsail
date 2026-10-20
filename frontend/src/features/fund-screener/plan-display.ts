import Decimal from "decimal.js";
import type {
  ActionableZoneResult,
  ActionableZoneTrack,
  EntrySafety,
  PlanPrice,
} from "../../api/investment";

export const revisionLabels = {
  INITIAL: "首次建立区间",
  MAINTAINED: "维持关注区间",
  UPGRADED: "上调关注区间",
  DOWNGRADED: "下修关注区间",
  TAKE_PROFIT_ALERT: "止盈复核提示",
};

/** Old snapshots express the same condition via state; WATCH is never entry-ready. */
export function entryReady(signal: {
  triggerState: string;
  inEntryZone: boolean;
  entryReady?: boolean;
  entryBlocked?: boolean;
  inBuyZone?: boolean;
}): boolean {
  return (
    signal.triggerState === "CANDIDATE" &&
    signal.inEntryZone &&
    signal.entryReady !== false &&
    !signal.entryBlocked &&
    signal.inBuyZone !== false
  );
}

export function entryAllowed(...rows: Array<EntrySafety | undefined>): boolean {
  return rows.every(
    (row) =>
      !row?.entryBlocked &&
      row?.entryReady !== false &&
      row?.inBuyZone !== false,
  );
}

/** A protected server status takes precedence over the geometric price position. */
export function planEntryStatus(
  result: ActionableZoneResult,
  track?: ActionableZoneTrack,
  signal?: EntrySafety,
): { text: string; warning: boolean } | null {
  const override = [result, signal, track].find((row) => row?.entryStatus);
  if (override?.entryStatus)
    return {
      text: override.entryStatus,
      warning: override.entryStatusTone === "amber" || !!override.entryBlocked,
    };
  if ([result, signal, track].some((row) => row?.entryBlocked))
    return { text: "⚠️ 暂缓入场", warning: true };
  if (
    track?.track === "RIGHT_TREND" &&
    result.currentPrice != null &&
    track.entryZoneMax != null &&
    Number.isFinite(Number(result.currentPrice)) &&
    Number.isFinite(Number(track.entryZoneMax)) &&
    Number(track.entryZoneMax) > 0 &&
    Number(result.currentPrice) > Number(track.entryZoneMax)
  ) {
    const gap = new Decimal(result.currentPrice)
      .div(track.entryZoneMax)
      .minus(1)
      .mul(100);
    return {
      text: `🚀 高于买区 ${gap.toFixed(2)}%·等待回踩均线`,
      warning: false,
    };
  }
  return null;
}

/** Reading projection only; do not mutate provider names or frozen snapshots. */
export function fundDisplayName(label: string, code?: string | null): string {
  if (!code || !/^\d{6}$/.test(code)) return label;
  return (
    label
      .replace(
        new RegExp(`^\\s*${code}(?:[\\s｜|:：·—-]+|(?=[\\u3400-\\u9fff]))`),
        "",
      )
      .trim() || label
  );
}

export function completeTrack(track: ActionableZoneTrack): boolean {
  return (
    track.state !== "INSUFFICIENT" &&
    [
      track.entryZoneMin,
      track.entryZoneMax,
      track.stopLossPrice,
      track.takeProfitT1,
      track.takeProfitT2,
    ].every(
      (value) =>
        value != null && Number.isFinite(Number(value)) && Number(value) > 0,
    ) &&
    Number(track.entryZoneMin) <= Number(track.entryZoneMax)
  );
}

export function entryDistance(
  currentPrice: PlanPrice,
  track: ActionableZoneTrack,
): number | null {
  if (
    currentPrice == null ||
    !Number.isFinite(Number(currentPrice)) ||
    Number(currentPrice) <= 0 ||
    track.entryZoneMin == null ||
    track.entryZoneMax == null
  )
    return null;
  const current = new Decimal(currentPrice);
  const minimum = new Decimal(track.entryZoneMin);
  const maximum = new Decimal(track.entryZoneMax);
  if (current.gte(minimum) && current.lte(maximum)) return 0;
  const boundary = current.lt(minimum) ? minimum : maximum;
  return boundary.minus(current).abs().div(current).mul(100).toNumber();
}

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
    ? "最新披露净值参考，存在跨市场发布延迟；不是当日估值，请留意最新外盘冲击。"
    : null;
}

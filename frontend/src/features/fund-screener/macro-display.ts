import type { MacroContext } from "../../api/investment";

export type MacroItem = MacroContext["items"][number];

export function metricValue(value?: number | null, maximumFractionDigits = 2) {
  return value == null || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits }).format(value);
}

const monthFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "numeric",
});
const timeFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

export function monthLabel(raw?: string | null) {
  if (!raw) return "未知月份";
  const period = /^(\d{4})-(\d{2})(?:-\d{2})?$/.exec(raw);
  if (period && Number(period[2]) >= 1 && Number(period[2]) <= 12)
    return `${period[1]}年${Number(period[2])}月`;
  const day = new Date(raw);
  return Number.isFinite(day.getTime())
    ? monthFormatter.format(day)
    : "未知月份";
}

export function timeLabel(raw?: string | null) {
  if (!raw) return "未确认";
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const parts = raw.split("-").map(Number);
    return `${parts[0]}年${parts[1]}月${parts[2]}日（时刻未提供）`;
  }
  const day = new Date(raw);
  if (!Number.isFinite(day.getTime())) return "未确认";
  const parts = Object.fromEntries(
    timeFormatter.formatToParts(day).map((p) => [p.type, p.value]),
  );
  return `${parts.year}年${parts.month}月${parts.day}日 ${parts.hour}:${parts.minute}`;
}

export function macroUnit(item: Pick<MacroItem, "code" | "unit">) {
  if (item.unit === "index_point") return "指数点";
  if (item.unit === "percent") return "%";
  if (item.unit) return item.unit;
  if (item.code === "PMI") return "指数点";
  return ["CPI_YOY", "PPI_YOY", "M2_YOY"].includes(item.code) ? "%" : "";
}

export function changeLabel(value: number | null | undefined, item: MacroItem) {
  if (value == null || !Number.isFinite(value)) return "暂不可计算";
  const unit = macroUnit(item) === "%" ? "个百分点" : macroUnit(item);
  return `${value > 0 ? "+" : ""}${value.toFixed(2)} ${unit}`.trim();
}

export function sourceHref(raw?: string | null) {
  try {
    const url = new URL(raw ?? "");
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}

export const valueVerificationLabels: Record<string, string> = {
  VERIFIED: "数值已核对官方数据",
  PENDING: "数值来源待核对",
  CONFLICT: "数值证据冲突",
  UNAVAILABLE: "暂无可靠数值",
  READY: "历史事实已采集",
  STALE: "旧期证据，需更新",
  NOT_COLLECTED: "数据尚未采集",
  UNVERIFIED: "数值来源待核对",
};

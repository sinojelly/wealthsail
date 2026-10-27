export function formatShanghaiTime(value: string | number | null): string {
  if (value === null) return "未知";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "未知";
  return date.toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

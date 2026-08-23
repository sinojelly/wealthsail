import Decimal from "decimal.js";

// 唯一允许把 DecimalString 转为 JS number 的边界；只供图表坐标使用，
// 永远不能将结果回写为交易、持仓或估值账本金额。
export function toChartNumber(value: string): number {
  const number = new Decimal(value).toNumber();
  if (!Number.isFinite(number)) throw new Error(`无法绘制非有限数值：${value}`);
  return number;
}

export function normalizeChartIndex(value: string, base: string): number {
  const normalized = new Decimal(value).div(base).mul(100);
  return toChartNumber(normalized.toDecimalPlaces(8).toString());
}

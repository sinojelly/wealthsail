import Decimal from "decimal.js";

Decimal.set({ precision: 32, rounding: Decimal.ROUND_HALF_UP });

export function addMoney(left: string, right: string): string {
  return new Decimal(left).plus(right).toFixed(2);
}

export function subtractMoney(left: string, right: string): string {
  return new Decimal(left).minus(right).toFixed(2);
}

export function formatMoney(value: string, minimumFractionDigits = 2): string {
  const decimal = new Decimal(value).toFixed(minimumFractionDigits);
  const [integer, fraction] = decimal.split(".");
  const formattedInteger = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `¥${formattedInteger}${fraction ? `.${fraction}` : ""}`;
}

export function formatSignedMoney(value: string): string {
  const decimal = new Decimal(value);
  return `${decimal.greaterThanOrEqualTo(0) ? "+" : "-"}${formatMoney(decimal.abs().toFixed(2))}`;
}

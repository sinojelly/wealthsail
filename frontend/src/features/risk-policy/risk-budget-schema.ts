import Decimal from "decimal.js";
import { z } from "zod";

function decimalBetween(
  value: string,
  minimum: string,
  maximum: string,
): boolean {
  try {
    const decimal = new Decimal(value);
    return (
      decimal.greaterThanOrEqualTo(minimum) &&
      decimal.lessThanOrEqualTo(maximum)
    );
  } catch {
    return false;
  }
}

function integerBetween(
  value: string,
  minimum: string,
  maximum: string,
): boolean {
  try {
    const decimal = new Decimal(value);
    return decimal.isInteger() && decimalBetween(value, minimum, maximum);
  } catch {
    return false;
  }
}

export const riskSchema = z.object({
  benchmark: z.string().min(1),
  horizonMonths: z
    .string()
    .refine(
      (value) => integerBetween(value, "3", "120"),
      "请输入 3–120 之间的整数",
    ),
  maxPosition: z
    .string()
    .refine(
      (value) => decimalBetween(value, "1", "100"),
      "请输入 1–100 之间的百分比",
    ),
  maxDrawdown: z
    .string()
    .refine(
      (value) => decimalBetween(value, "1", "80"),
      "请输入 1–80 之间的百分比",
    ),
  minCash: z
    .string()
    .refine(
      (value) => decimalBetween(value, "0", "100"),
      "请输入 0–100 之间的百分比",
    ),
  maxAlertsPerDay: z
    .string()
    .refine(
      (value) => integerBetween(value, "1", "20"),
      "请输入 1–20 之间的整数",
    ),
  silenceMinutes: z
    .string()
    .refine(
      (value) => integerBetween(value, "5", "1440"),
      "请输入 5–1440 之间的整数",
    ),
});

export type RiskBudgetValues = z.infer<typeof riskSchema>;

export const initialRiskBudget: RiskBudgetValues = {
  benchmark: "CSI300",
  horizonMonths: "24",
  maxPosition: "30",
  maxDrawdown: "15",
  minCash: "5",
  maxAlertsPerDay: "5",
  silenceMinutes: "60",
};

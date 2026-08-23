import Decimal from "decimal.js";
import type { RiskPreviewDto } from "./generated/types";

// TARGET: 替换为服务端 policy preview，前端只展示确定性结果和 policy_hash。
export async function previewRiskPolicy(
  nextMaxPosition: string,
): Promise<RiskPreviewDto> {
  const next = new Decimal(nextMaxPosition);
  return {
    previousMaxPosition: "30.00",
    nextMaxPosition: next.toFixed(2),
    impactedHoldings: next.lt(30) ? 1 : 0,
    newAlerts: next.lt(25) ? 2 : 0,
    policyHash: `sha256:demo-${next.toFixed(2).replace(".", "")}`,
  };
}

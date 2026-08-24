import Decimal from "decimal.js";
import { request } from "./client";
import type {
  PoliciesResponse,
  RiskPreviewDto,
  RulesResponse,
} from "./types";

export async function getPolicies(): Promise<PoliciesResponse> {
  const envelope = await request<PoliciesResponse>("/v1/risk/policies");
  return envelope.data;
}

export async function getRules(): Promise<RulesResponse> {
  const envelope = await request<RulesResponse>("/v1/risk/rules");
  return envelope.data;
}

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

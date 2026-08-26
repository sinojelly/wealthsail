import { request } from "./client";
import type {
  PoliciesResponse,
  RiskConfirmDto,
  RiskPreviewDto,
  RulesResponse,
} from "./types";

export interface RiskPolicyInput {
  benchmark: string;
  horizonMonths: number;
  maxPosition: string;
  maxDrawdown: string;
  minCash: string;
  maxAlertsPerDay: number;
  silenceMinutes: number;
}

export async function getPolicies(): Promise<PoliciesResponse> {
  const envelope = await request<PoliciesResponse>("/v1/risk/policies");
  return envelope.data;
}

export async function getRules(): Promise<RulesResponse> {
  const envelope = await request<RulesResponse>("/v1/risk/rules");
  return envelope.data;
}

export async function previewRiskPolicy(
  input: RiskPolicyInput,
): Promise<RiskPreviewDto> {
  const envelope = await request<RiskPreviewDto>("/v1/risk/policies/preview", {
    method: "POST",
    headers: { "Idempotency-Key": crypto.randomUUID() },
    body: JSON.stringify(input),
  });
  return envelope.data;
}

export async function confirmRiskPolicy(input: {
  draftId: string;
  expectedVersion: number;
  policyHash: string;
  password: string;
}): Promise<RiskConfirmDto> {
  const envelope = await request<RiskConfirmDto>("/v1/risk/policies/confirm", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return envelope.data;
}

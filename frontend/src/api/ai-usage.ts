import { useQuery } from "@tanstack/react-query";
import { privateQueryKey, useAuthSession } from "../app/auth-session";
import { request } from "./client";

export interface AiQuota {
  windows: {
    key: string;
    label: string;
    consumedPercent: number | null;
    status: string;
  }[];
  credits: { consumed: string | null; status: string };
}

export interface AiTokens {
  inputTokens: number | null;
  cachedTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
}

export interface AiUsageCall extends AiTokens {
  id: string;
  model: string;
  callType: string;
  startedAt: string;
  finishedAt: string | null;
  status: "SUCCEEDED" | "FAILED" | "RUNNING" | "UNCERTAIN";
  errorClass: string | null;
  quota: AiQuota;
}

export interface AiUsageTask extends AiTokens {
  id: string;
  taskType: string;
  taskName: string;
  startedAt: string;
  finishedAt: string | null;
  inferenceStatus:
    "SUCCEEDED" | "FAILED" | "PARTIAL" | "RUNNING" | "UNCERTAIN" | "NONE";
  businessStatus: "SUCCEEDED" | "FAILED" | "DEGRADED" | "RUNNING" | "CANCELLED";
  models: string[];
  callCount: number;
  quota: AiQuota;
  calls: AiUsageCall[];
}

export interface AiUsageResponse {
  account: {
    status:
      | "READY"
      | "UNAVAILABLE"
      | "UNAUTHENTICATED"
      | "NOT_CONFIGURED"
      | "RESTRICTED";
    provider: string;
    email: string | null;
    planType: string | null;
    authType: string | null;
    observedAt: string | null;
    windows: {
      key: string;
      label: string;
      usedPercent: number | null;
      remainingPercent: number | null;
      windowMinutes: number | null;
      resetsAt: number | null;
    }[];
    credits: {
      balance: string | null;
      hasCredits: boolean | null;
      unlimited: boolean | null;
    } | null;
    limitations: string[];
  };
  tasks: AiUsageTask[];
  total: number;
  limit: number;
  offset: number;
  limitations: string[];
}

export function useAiUsage(enabled: boolean, offset = 0) {
  const session = useAuthSession();
  return useQuery({
    queryKey: privateQueryKey(session, "operations", "ai-usage", 20, offset),
    queryFn: async () =>
      (
        await request<AiUsageResponse>(
          `/v1/operations/ai-usage?limit=20&offset=${offset}`,
        )
      ).data,
    enabled,
    retry: false,
    staleTime: 30_000,
    refetchInterval: enabled ? 30_000 : false,
  });
}

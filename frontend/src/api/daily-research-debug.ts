import { request } from "./client";
import type { DailyResearch } from "./investment";
export interface DebugResearchRequest extends Pick<
  DailyResearch,
  | "failureStage"
  | "failureReasonCode"
  | "failureRuleId"
  | "failureItemIndex"
  | "failureIndustryCodes"
> {
  id: string;
  status:
    | "QUEUED"
    | "RUNNING"
    | "READY"
    | "FAILED"
    | "UNCERTAIN"
    | "CANCELLED"
    | "EXPIRED";
  requestMode?: "DEBUG" | "MANUAL";
  requestedAt: string;
  startedAt: string | null;
  completedAt: string | null;
  batchId: string | null;
  reportRunId: string | null;
  errorClass: string | null;
  queueExpiresAt?: string;
  queueWaitSeconds?: number;
  blockedReasonCode?:
    "MODEL_GUARD_ACTIVE" | "REQUEST_RUNNING" | "WAITING_WORKER" | null;
  blockedSince?: string | null;
  queuePosition?: number | null;
  canCancel?: boolean;
  executionStage?:
    | "COLLECTING"
    | "WAITING_MODEL_SLOT"
    | "MODEL_RUNNING"
    | "VERIFYING_OUTPUT"
    | "REPORT_VALIDATION"
    | "PERSISTING"
    | null;
  stageUpdatedAt?: string | null;
  modelExitEvidenceState?:
    | "LOCAL_RUNNING"
    | "LOCAL_EXIT_CONFIRMED"
    | "LOCAL_EXIT_UNCONFIRMED"
    | "LOCAL_EVIDENCE_NOT_RETAINED"
    | "LOCAL_PLATFORM_UNSUPPORTED"
    | null;
  modelExitEvidenceCheckedAt?: string | null;
}
const path = "/v1/investment/daily-research/refresh";
export async function getResearchRefresh(signal?: AbortSignal) {
  return (
    await request<{ enabled: boolean; request: DebugResearchRequest | null }>(
      path,
      { signal },
    )
  ).data;
}
export async function startResearchRefresh(commandKey: string) {
  return (
    await request<DebugResearchRequest>(path, {
      method: "POST",
      body: JSON.stringify({ commandKey }),
    })
  ).data;
}
export async function getResearchRefreshStatus(
  id: string,
  signal?: AbortSignal,
) {
  return (
    await request<DebugResearchRequest>(`${path}/${encodeURIComponent(id)}`, {
      signal,
    })
  ).data;
}

export async function cancelResearchRefresh(id: string) {
  return (
    await request<DebugResearchRequest>(
      `${path}/${encodeURIComponent(id)}/cancel`,
      {
        method: "POST",
        body: JSON.stringify({}),
      },
    )
  ).data;
}

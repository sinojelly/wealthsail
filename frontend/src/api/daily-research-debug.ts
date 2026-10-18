import { request } from "./client";
export interface DebugResearchRequest {
  id: string;
  status: "QUEUED" | "RUNNING" | "READY" | "FAILED" | "UNCERTAIN";
  requestedAt: string;
  startedAt: string | null;
  completedAt: string | null;
  batchId: string | null;
  reportRunId: string | null;
  errorClass: string | null;
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

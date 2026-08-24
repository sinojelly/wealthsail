import { request } from "./client";
import type {
  AuditRow,
  FreshnessResponse,
  OpsJobsResponse,
  OutboxResponse,
} from "./types";

export async function getOpsJobs(): Promise<OpsJobsResponse> {
  const envelope = await request<OpsJobsResponse>("/v1/operations/jobs");
  return envelope.data;
}

export async function getOpsFreshness(): Promise<FreshnessResponse> {
  const envelope = await request<FreshnessResponse>("/v1/operations/freshness");
  return envelope.data;
}

export async function getOpsOutbox(): Promise<OutboxResponse> {
  const envelope = await request<OutboxResponse>("/v1/operations/outbox");
  return envelope.data;
}

export async function getOpsAudit(limit = 50): Promise<AuditRow[]> {
  const envelope = await request<AuditRow[]>(
    `/v1/operations/audit?limit=${limit}`,
  );
  return envelope.data;
}

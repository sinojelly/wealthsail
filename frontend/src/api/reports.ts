import { request } from "./client";
import type { ReportDetail, ReportSession, ReportSummary } from "./types";

export const reportQueryKeys = {
  all: ["reports"] as const,
  list: (tradeDate?: string, session?: string) =>
    ["reports", "list", tradeDate ?? "", session ?? ""] as const,
  detail: (id: string) => ["reports", id] as const,
};

export async function listReports(params?: {
  tradeDate?: string;
  session?: ReportSession;
}): Promise<ReportSummary[]> {
  const query = new URLSearchParams();
  if (params?.tradeDate) query.set("tradeDate", params.tradeDate);
  if (params?.session) query.set("session", params.session);
  const suffix = query.size ? `?${query.toString()}` : "";
  const envelope = await request<ReportSummary[]>(`/v1/reports/list${suffix}`);
  return envelope.data;
}

export async function getReport(id: string): Promise<ReportDetail> {
  const envelope = await request<ReportDetail>(`/v1/reports/${id}`);
  return envelope.data;
}

export async function generateReport(
  session: ReportSession,
): Promise<ReportDetail> {
  const envelope = await request<ReportDetail>("/v1/reports/generate", {
    method: "POST",
    body: JSON.stringify({ session }),
  });
  return envelope.data;
}

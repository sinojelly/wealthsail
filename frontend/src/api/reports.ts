import { request } from "./client";
import type { ReportDetail, ReportSession, ReportSummary } from "./types";
import type { AuthSession } from "../app/auth-session";
import { privateQueryKey } from "../app/auth-session";

export const reportQueryKeys = {
  all: (auth: AuthSession | null) => privateQueryKey(auth, "reports"),
  list: (auth: AuthSession | null, tradeDate?: string, session?: string) =>
    privateQueryKey(auth, "reports", "list", tradeDate ?? "", session ?? ""),
  detail: (auth: AuthSession | null, id: string) =>
    privateQueryKey(auth, "reports", "detail", id),
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

export interface TriggerReportResult {
  reportId: string | null;
  outboxId: string | null;
}

/**
 * 触发一次报告生成（服务端 force 生成新版本），写入 outbox 后由 Worker 推送企微。
 *
 * 走 debug 触发端点而非 /v1/reports/generate：后者 force=false，当天已有报告时
 * 直接返回旧版本，既不生成新版本也不写 outbox（不会推送）。
 *
 * ``phase`` 决定触发的变体：选中行若是
 * ``PRE_MARKET_MACRO``，传 ``{ phase: "MACRO" }``；若是 ``PRE_MARKET_PERSONALIZED``，
 * 传 ``{ phase: "PERSONALIZED" }``。服务端始终以当前登录用户为报告受众和唯一
 * 投递目标，前端不能代替其他成员触发通知。
 */
export async function triggerReport(
  session: ReportSession,
  options: { phase?: "MACRO" | "PERSONALIZED" | "ALERT" } = {},
): Promise<TriggerReportResult> {
  const params = new URLSearchParams();
  if (options.phase) {
    params.set("phase", options.phase);
  }
  const query = params.toString();
  const envelope = await request<TriggerReportResult>(
    `/v1/debug/reports/trigger/${encodeURIComponent(session)}${
      query ? `?${query}` : ""
    }`,
    { method: "POST" },
  );
  return envelope.data;
}

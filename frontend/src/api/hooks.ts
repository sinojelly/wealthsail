import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { HttpError } from "./client";
import { logout } from "../app/auth-session";
import * as identityApi from "./identity";
import * as operationsApi from "./operations";
import * as portfolioApi from "./portfolio";
import * as reportsApi from "./reports";
import { reportQueryKeys } from "./reports";
import * as riskApi from "./risk";
import type {
  AuditRow,
  BindingCredential,
  FreshnessResponse,
  LoginResponse,
  MeResponse,
  NavTrend,
  OpsJobsResponse,
  OutboxResponse,
  PoliciesResponse,
  PortfolioSummary,
  PositionDto,
  ReportDetail,
  ReportSession,
  ReportSummary,
  RulesResponse,
} from "./types";

/** 401 统一处理：令牌失效 → 清会话（页面守卫会自动跳转登录页）。 */
function onAuthFailure(error: unknown) {
  if (error instanceof HttpError && error.status === 401) {
    logout();
  }
}

export function usePortfolioSummary(): UseQueryResult<PortfolioSummary> {
  return useQuery({
    queryKey: ["portfolio", "summary"],
    queryFn: portfolioApi.getSummary,
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function usePositions(): UseQueryResult<PositionDto[]> {
  return useQuery({
    queryKey: ["portfolio", "positions"],
    queryFn: portfolioApi.getPositions,
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function useNavTrend(days = 60): UseQueryResult<NavTrend> {
  return useQuery({
    queryKey: ["portfolio", "nav-trend", days],
    queryFn: () => portfolioApi.getNavTrend(days),
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function useReportsList(params?: {
  tradeDate?: string;
  session?: ReportSession;
}): UseQueryResult<ReportSummary[]> {
  return useQuery({
    queryKey: reportQueryKeys.list(params?.tradeDate, params?.session),
    queryFn: () => reportsApi.listReports(params),
  });
}

export function useReportDetail(
  id: string | null,
): UseQueryResult<ReportDetail> {
  return useQuery({
    queryKey: reportQueryKeys.detail(id ?? ""),
    queryFn: () => reportsApi.getReport(id as string),
    enabled: Boolean(id),
  });
}

export function useGenerateReport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (session: ReportSession) => reportsApi.generateReport(session),
    onSuccess: (report) => {
      queryClient.invalidateQueries({ queryKey: reportQueryKeys.all });
      queryClient.setQueryData(
        reportQueryKeys.detail(report.id),
        report,
      );
    },
  });
}

export function usePolicies(): UseQueryResult<PoliciesResponse> {
  return useQuery({
    queryKey: ["risk", "policies"],
    queryFn: riskApi.getPolicies,
  });
}

export function useRules(): UseQueryResult<RulesResponse> {
  return useQuery({
    queryKey: ["risk", "rules"],
    queryFn: riskApi.getRules,
  });
}

export function useMe(): UseQueryResult<MeResponse> {
  return useQuery({
    queryKey: ["identity", "me"],
    queryFn: identityApi.getMe,
  });
}

export function useLogin() {
  return useMutation({
    mutationFn: ({
      email,
      password,
    }: {
      email: string;
      password: string;
    }): Promise<LoginResponse> => identityApi.login(email, password),
    onError: onAuthFailure,
  });
}

export function useCreateBindingCredential() {
  return useMutation({
    mutationFn: (): Promise<BindingCredential> =>
      identityApi.createBindingCredential(),
    onError: onAuthFailure,
  });
}

export function useConsumeBindingCredential() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      token: string;
      externalUserId: string;
      externalUsername?: string;
    }) => identityApi.consumeBindingCredential(input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["identity", "me"] });
    },
    onError: onAuthFailure,
  });
}

export function useOpsJobs(): UseQueryResult<OpsJobsResponse> {
  return useQuery({
    queryKey: ["operations", "jobs"],
    queryFn: operationsApi.getOpsJobs,
  });
}

export function useOpsFreshness(): UseQueryResult<FreshnessResponse> {
  return useQuery({
    queryKey: ["operations", "freshness"],
    queryFn: operationsApi.getOpsFreshness,
  });
}

export function useOpsOutbox(): UseQueryResult<OutboxResponse> {
  return useQuery({
    queryKey: ["operations", "outbox"],
    queryFn: operationsApi.getOpsOutbox,
  });
}

export function useOpsAudit(limit = 50): UseQueryResult<AuditRow[]> {
  return useQuery({
    queryKey: ["operations", "audit", limit],
    queryFn: () => operationsApi.getOpsAudit(limit),
  });
}

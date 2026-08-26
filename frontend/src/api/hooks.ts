import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { HttpError } from "./client";
import {
  logout,
  privateQueryKey,
  useAuthSession,
} from "../app/auth-session";
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
    void logout();
  }
}

function usePrivateSession() {
  return useAuthSession();
}

export function usePortfolioSummary(): UseQueryResult<PortfolioSummary> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "portfolio", "summary"),
    queryFn: portfolioApi.getSummary,
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function usePositions(): UseQueryResult<PositionDto[]> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "portfolio", "positions"),
    queryFn: portfolioApi.getPositions,
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function useNavTrend(days = 60): UseQueryResult<NavTrend> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "portfolio", "nav-trend", days),
    queryFn: () => portfolioApi.getNavTrend(days),
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function useReportsList(params?: {
  tradeDate?: string;
  session?: ReportSession;
}): UseQueryResult<ReportSummary[]> {
  const auth = usePrivateSession();
  return useQuery({
    queryKey: reportQueryKeys.list(auth, params?.tradeDate, params?.session),
    queryFn: () => reportsApi.listReports(params),
  });
}

export function useReportDetail(
  id: string | null,
): UseQueryResult<ReportDetail> {
  const auth = usePrivateSession();
  return useQuery({
    queryKey: reportQueryKeys.detail(auth, id ?? ""),
    queryFn: () => reportsApi.getReport(id as string),
    enabled: Boolean(id),
  });
}

export function useGenerateReport() {
  const queryClient = useQueryClient();
  const auth = usePrivateSession();
  return useMutation({
    mutationFn: (session: ReportSession) => reportsApi.generateReport(session),
    onSuccess: (report) => {
      queryClient.invalidateQueries({ queryKey: reportQueryKeys.all(auth) });
      queryClient.setQueryData(
        reportQueryKeys.detail(auth, report.id),
        report,
      );
    },
  });
}

export function usePolicies(): UseQueryResult<PoliciesResponse> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "risk", "policies"),
    queryFn: riskApi.getPolicies,
  });
}

export function useRules(): UseQueryResult<RulesResponse> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "risk", "rules"),
    queryFn: riskApi.getRules,
  });
}

export function useMe(): UseQueryResult<MeResponse> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "identity", "me"),
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

export function useRevokeBinding() {
  const queryClient = useQueryClient();
  const session = usePrivateSession();
  return useMutation({
    mutationFn: (input: { bindingId: string; password: string }) =>
      identityApi.revokeBinding(input.bindingId, input.password),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: privateQueryKey(session, "identity", "me") });
    },
    onError: onAuthFailure,
  });
}

export function useOpsJobs(): UseQueryResult<OpsJobsResponse> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "operations", "jobs"),
    queryFn: operationsApi.getOpsJobs,
    refetchInterval: 30_000,
  });
}

export function useOpsFreshness(): UseQueryResult<FreshnessResponse> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "operations", "freshness"),
    queryFn: operationsApi.getOpsFreshness,
    refetchInterval: 30_000,
  });
}

export function useOpsOutbox(): UseQueryResult<OutboxResponse> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "operations", "outbox"),
    queryFn: operationsApi.getOpsOutbox,
    refetchInterval: 30_000,
  });
}

export function useOpsAudit(limit = 50): UseQueryResult<AuditRow[]> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "operations", "audit", limit),
    queryFn: () => operationsApi.getOpsAudit(limit),
    refetchInterval: 30_000,
  });
}

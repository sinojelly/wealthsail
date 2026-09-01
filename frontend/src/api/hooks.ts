import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { HttpError, request } from "./client";
import { logout, privateQueryKey, useAuthSession } from "../app/auth-session";
import * as identityApi from "./identity";
import * as marketApi from "./market";
import * as operationsApi from "./operations";
import * as portfolioApi from "./portfolio";
import * as reportsApi from "./reports";
import { reportQueryKeys } from "./reports";
import * as riskApi from "./risk";
import * as watchlistApi from "./watchlist";
import type {
  AuditRow,
  BindingCredential,
  FreshnessResponse,
  HoldingStartEstimateConfirmation,
  InstrumentSearchResult,
  InvitationCredential,
  InvitableTenantRole,
  LoginResponse,
  MeResponse,
  NavTrend,
  OpsJobsResponse,
  OutboxResponse,
  PoliciesResponse,
  PortfolioSummary,
  PerformanceBaselineDto,
  PortfolioAssetScope,
  PositionDto,
  PositionMutationPayload,
  ReportDetail,
  ReportSubscriptionUpdate,
  ReportSession,
  ReportSummary,
  RulesResponse,
  TiantianConfirmPayload,
  TiantianImportDetail,
  TiantianImportList,
  TiantianDuplicatePolicy,
  WatchlistItem,
} from "./types";

const MINE_SCOPE: PortfolioAssetScope = { kind: "MINE" };

/** 401 统一处理：令牌失效 → 清会话（页面守卫会自动跳转登录页）。 */
function onAuthFailure(error: unknown) {
  if (error instanceof HttpError && error.status === 401) {
    void logout();
  }
}

function usePrivateSession() {
  return useAuthSession();
}

export function usePortfolioSummary(
  scope: PortfolioAssetScope = MINE_SCOPE,
): UseQueryResult<PortfolioSummary> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(
      session,
      "portfolio",
      "summary",
      scope.kind,
      scope.memberId ?? "",
    ),
    queryFn: () => portfolioApi.getSummary(scope),
    refetchInterval: (query) =>
      query.state.data?.valuationStatus === "SYNCING" ? 2_000 : false,
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function usePerformanceBaselines(): UseQueryResult<
  PerformanceBaselineDto[]
> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "portfolio", "performance-baselines"),
    queryFn: portfolioApi.getPerformanceBaselines,
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function useRevisePerformanceBaseline() {
  const queryClient = useQueryClient();
  const session = usePrivateSession();
  return useMutation({
    mutationFn: portfolioApi.revisePerformanceBaseline,
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: privateQueryKey(
          session,
          "portfolio",
          "performance-baselines",
        ),
      });
      void queryClient.invalidateQueries({
        queryKey: privateQueryKey(session, "portfolio", "summary"),
      });
    },
    onError: onAuthFailure,
  });
}

export function usePositions(
  scope: PortfolioAssetScope = MINE_SCOPE,
): UseQueryResult<PositionDto[]> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(
      session,
      "portfolio",
      "positions",
      scope.kind,
      scope.memberId ?? "",
    ),
    queryFn: () => portfolioApi.getPositions(scope),
    refetchInterval: (query) =>
      query.state.data?.some(
        (position) => position.valuationStatus === "SYNCING",
      )
        ? 2_000
        : false,
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function useNavTrend(
  days = 60,
  scope: PortfolioAssetScope = MINE_SCOPE,
  refreshWhileValuationSyncs = false,
): UseQueryResult<NavTrend> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(
      session,
      "portfolio",
      "nav-trend",
      days,
      scope.kind,
      scope.memberId ?? "",
    ),
    queryFn: () => portfolioApi.getNavTrend(days, scope),
    refetchInterval: refreshWhileValuationSyncs ? 2_000 : false,
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function useInstrumentSearch(
  keyword: string,
  enabled = true,
): UseQueryResult<InstrumentSearchResult[]> {
  const session = usePrivateSession();
  const trimmed = keyword.trim();
  return useQuery({
    queryKey: privateQueryKey(session, "portfolio", "search", trimmed),
    queryFn: () => portfolioApi.searchInstruments(trimmed),
    enabled: enabled && trimmed.length >= 2,
    staleTime: 60_000,
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

function watchlistQueryKey(session: ReturnType<typeof usePrivateSession>) {
  return privateQueryKey(session, "watchlist", "mine");
}

export function useWatchlist(): UseQueryResult<WatchlistItem[]> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: watchlistQueryKey(session),
    queryFn: watchlistApi.list,
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function useAddWatchlist() {
  const queryClient = useQueryClient();
  const session = usePrivateSession();
  return useMutation({
    mutationFn: watchlistApi.add,
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: watchlistQueryKey(session),
      });
    },
    onError: onAuthFailure,
  });
}

export function useRemoveWatchlist() {
  const queryClient = useQueryClient();
  const session = usePrivateSession();
  return useMutation({
    mutationFn: watchlistApi.remove,
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: watchlistQueryKey(session),
      });
    },
    onError: onAuthFailure,
  });
}

/** 持仓变更后统一刷新看板三件套（持仓列表 / 总览 / 净值走势）。 */
function usePortfolioInvalidate() {
  const queryClient = useQueryClient();
  const session = usePrivateSession();
  return () => {
    void queryClient.invalidateQueries({
      queryKey: privateQueryKey(session, "portfolio", "positions"),
    });
    void queryClient.invalidateQueries({
      queryKey: privateQueryKey(session, "portfolio", "summary"),
    });
    void queryClient.invalidateQueries({
      queryKey: privateQueryKey(session, "portfolio", "nav-trend"),
    });
    void queryClient.invalidateQueries({
      queryKey: privateQueryKey(session, "research", "recommendations"),
    });
  };
}

export function useAddPosition() {
  return useMutation({
    mutationFn: ({
      payload,
      idempotencyKey,
    }: {
      payload: PositionMutationPayload;
      idempotencyKey: string;
    }) => portfolioApi.createPositionImport(payload, idempotencyKey),
    onError: onAuthFailure,
  });
}

export function useConfirmPositionImport() {
  const invalidate = usePortfolioInvalidate();
  return useMutation({
    mutationFn: ({
      batchId,
      idempotencyKey,
    }: {
      batchId: string;
      idempotencyKey: string;
    }) => portfolioApi.confirmPositionImport(batchId, idempotencyKey),
    onSuccess: invalidate,
    onError: onAuthFailure,
  });
}

export function useCsvPositionImport() {
  const invalidate = usePortfolioInvalidate();
  return useMutation({
    mutationFn: ({
      csvData,
      idempotencyKey,
      baselineDate,
    }: {
      csvData: string;
      idempotencyKey: string;
      baselineDate?: string;
    }) =>
      portfolioApi.submitCsvPositions(csvData, idempotencyKey, baselineDate),
    onSuccess: invalidate,
    onError: onAuthFailure,
  });
}

export function useTiantianImports(
  options: { cursor?: string; limit?: number; status?: "DRAFT" } = {},
): UseQueryResult<TiantianImportList> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(
      session,
      "portfolio",
      "tiantian-imports",
      options.status ?? "ALL",
      options.cursor ?? "FIRST",
      options.limit ?? 20,
    ),
    queryFn: () => portfolioApi.listTiantianImports(options),
    retry: (count, error) =>
      !(error instanceof HttpError && error.status === 401) && count < 1,
  });
}

export function useTiantianImport(
  batchId: string | null,
): UseQueryResult<TiantianImportDetail> {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(
      session,
      "portfolio",
      "tiantian-import",
      batchId ?? "",
    ),
    queryFn: () => portfolioApi.getTiantianImport(batchId as string),
    enabled: Boolean(batchId),
    refetchInterval: (query) => {
      const detail = query.state.data;
      return detail?.status === "CONFIRMED" &&
        detail.metricsStatus === "PENDING"
        ? 2_000
        : false;
    },
  });
}

export function usePreviewTiantianImport() {
  const queryClient = useQueryClient();
  const session = usePrivateSession();
  return useMutation({
    mutationFn: ({
      file,
      accountName,
      accountId,
      idempotencyKey,
      duplicatePolicy,
    }: {
      file: File;
      accountName?: string;
      accountId?: string;
      idempotencyKey: string;
      duplicatePolicy?: TiantianDuplicatePolicy;
    }) =>
      portfolioApi.previewTiantianImport(
        file,
        { accountName, accountId },
        idempotencyKey,
        duplicatePolicy,
      ),
    onSuccess: (detail) => {
      queryClient.setQueryData(
        privateQueryKey(
          session,
          "portfolio",
          "tiantian-import",
          detail.batchId,
        ),
        detail,
      );
      void queryClient.invalidateQueries({
        queryKey: privateQueryKey(session, "portfolio", "tiantian-imports"),
      });
    },
    onError: onAuthFailure,
  });
}

export function useConfirmTiantianImport() {
  const queryClient = useQueryClient();
  const session = usePrivateSession();
  const invalidatePortfolio = usePortfolioInvalidate();
  return useMutation({
    mutationFn: ({
      batchId,
      version,
      payload,
      idempotencyKey,
    }: {
      batchId: string;
      version: number;
      payload: TiantianConfirmPayload;
      idempotencyKey: string;
    }) =>
      portfolioApi.confirmTiantianImport(
        batchId,
        version,
        payload,
        idempotencyKey,
      ),
    onSuccess: (result) => {
      invalidatePortfolio();
      void queryClient.invalidateQueries({
        queryKey: privateQueryKey(
          session,
          "portfolio",
          "tiantian-import",
          result.batchId,
        ),
      });
      void queryClient.invalidateQueries({
        queryKey: privateQueryKey(session, "portfolio", "tiantian-imports"),
      });
    },
    onError: onAuthFailure,
  });
}

export function useCancelTiantianImport() {
  const queryClient = useQueryClient();
  const session = usePrivateSession();
  return useMutation({
    mutationFn: ({
      batchId,
      version,
      idempotencyKey,
    }: {
      batchId: string;
      version: number;
      idempotencyKey: string;
    }) => portfolioApi.cancelTiantianImport(batchId, version, idempotencyKey),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({
        queryKey: privateQueryKey(
          session,
          "portfolio",
          "tiantian-import",
          result.batchId,
        ),
      });
      void queryClient.invalidateQueries({
        queryKey: privateQueryKey(session, "portfolio", "tiantian-imports"),
      });
    },
    onError: onAuthFailure,
  });
}

export function useRetryTiantianMetrics() {
  const queryClient = useQueryClient();
  const session = usePrivateSession();
  return useMutation({
    mutationFn: ({
      batchId,
      idempotencyKey,
    }: {
      batchId: string;
      idempotencyKey: string;
    }) => portfolioApi.retryTiantianMetrics(batchId, idempotencyKey),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({
        queryKey: privateQueryKey(
          session,
          "portfolio",
          "tiantian-import",
          result.batchId,
        ),
      });
    },
    onError: onAuthFailure,
  });
}

export function useConfirmTiantianHoldingStart() {
  const invalidatePortfolio = usePortfolioInvalidate();
  return useMutation({
    mutationFn: ({
      accountId,
      instrumentId,
      startedOn,
      idempotencyKey,
    }: {
      accountId: string;
      instrumentId: string;
      startedOn: string;
      idempotencyKey: string;
    }) =>
      portfolioApi.confirmTiantianHoldingStart(
        accountId,
        instrumentId,
        startedOn,
        idempotencyKey,
      ),
    onSuccess: invalidatePortfolio,
    onError: onAuthFailure,
  });
}

export function useConfirmTiantianHoldingStartEstimates() {
  const invalidatePortfolio = usePortfolioInvalidate();
  return useMutation({
    mutationFn: ({
      items,
      idempotencyKey,
    }: {
      items: HoldingStartEstimateConfirmation[];
      idempotencyKey: string;
    }) =>
      portfolioApi.confirmTiantianHoldingStartEstimates(items, idempotencyKey),
    onSuccess: invalidatePortfolio,
    onError: onAuthFailure,
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
      queryClient.setQueryData(reportQueryKeys.detail(auth, report.id), report);
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

export function useUpdateReportSubscriptions() {
  const queryClient = useQueryClient();
  const session = usePrivateSession();
  const meKey = privateQueryKey(session, "identity", "me");
  return useMutation({
    mutationFn: (input: ReportSubscriptionUpdate) =>
      identityApi.updateReportSubscriptions(input),
    onSuccess: (reportSubscriptions) => {
      queryClient.setQueryData<MeResponse>(meKey, (current) =>
        current ? { ...current, reportSubscriptions } : current,
      );
    },
    onError: onAuthFailure,
  });
}

export function useUpdateTenantName() {
  return useMutation({
    mutationFn: (input: { name: string }) =>
      identityApi.updateTenantName(input),
    onError: onAuthFailure,
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

export function useCreateInvitation() {
  return useMutation({
    mutationFn: (input: {
      email: string;
      role: InvitableTenantRole;
    }): Promise<InvitationCredential> => identityApi.createInvitation(input),
    onError: onAuthFailure,
  });
}

export function useCreatePersonalTenantInvitation() {
  return useMutation({
    mutationFn: (input: { email: string; tenantName: string }) =>
      identityApi.createPersonalTenantInvitation(input),
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
      queryClient.invalidateQueries({
        queryKey: privateQueryKey(session, "identity", "me"),
      });
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

export function useMarketSentiment() {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "market", "sentiment"),
    queryFn: marketApi.getMarketSentiment,
    refetchInterval: 300_000, // 5 分钟刷新（行情快照 60s 缓存，避免频繁拉取）
    staleTime: 120_000,
    retry: 1,
  });
}

export interface AiAdvice {
  items: Array<{
    id: string;
    action: "观察" | "持有候选" | "复核" | "减仓候选" | "加仓候选" | "暂不建议";
    title: string;
    detail: string;
    counterCase: string | null;
    confidence: string | null;
    riskLevel: "低" | "中" | "高";
    policyCheckStatus: "PASSED" | "WARNING";
    evidenceCount: number;
    evidenceQuality: string;
    validUntil: string;
    methodVersion: string;
  }>;
  status: "PUBLISHED" | "ABSTAIN";
  reasonCode: string | null;
  qualityStatus: string;
  disclaimer: string;
  generatedAt: string | null;
  model: string | null;
}

async function getChatAdvice(): Promise<AiAdvice> {
  const envelope = await request<AiAdvice>(
    "/v1/research/recommendations/latest",
  );
  return envelope.data;
}

export function useAiAdvice() {
  const session = usePrivateSession();
  return useQuery({
    queryKey: privateQueryKey(session, "research", "recommendations"),
    queryFn: getChatAdvice,
    staleTime: 300_000, // 读取已发布结果；GET 不触发模型调用。
    retry: 1,
    refetchOnWindowFocus: false,
  });
}

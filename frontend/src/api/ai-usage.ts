import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { privateQueryKey, useAuthSession } from "../app/auth-session";
import { HttpError, request } from "./client";

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
      | "STALE"
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

export type AiAccount = AiUsageResponse["account"];
export type AiUsageTasksResponse = Omit<AiUsageResponse, "account">;
export type AiAccountResponse = Pick<AiUsageResponse, "account">;

export function useAiUsage(enabled: boolean, offset = 0) {
  const session = useAuthSession();
  return useQuery({
    queryKey: privateQueryKey(
      session,
      "operations",
      "ai-usage",
      "tasks",
      20,
      offset,
    ),
    queryFn: async ({ signal }) =>
      (
        await request<AiUsageTasksResponse>(
          `/v1/operations/ai-usage?view=tasks&limit=20&offset=${offset}`,
          { signal },
        )
      ).data,
    enabled,
    retry: false,
    staleTime: 30_000,
    refetchInterval: enabled ? 30_000 : false,
  });
}

export function useAiAccount(enabled: boolean) {
  const session = useAuthSession();
  const queryClient = useQueryClient();
  const queryKey = privateQueryKey(
    session,
    "operations",
    "ai-usage",
    "account",
  );
  async function readAccount(
    requestedKey: typeof queryKey,
    force = false,
    signal?: AbortSignal,
  ): Promise<AiAccount> {
    try {
      const incoming = (
        await request<AiAccountResponse>(
          `/v1/operations/ai-usage?view=account${force ? "&refresh_account=true" : ""}`,
          { signal },
        )
      ).data.account;
      const previous = queryClient.getQueryData<AiAccount>(requestedKey);
      if (
        incoming.status === "UNAVAILABLE" &&
        !incoming.limitations.includes("ACCOUNT_AUTH_CHANGED") &&
        !incoming.limitations.includes("CODEX_NOT_CONFIGURED") &&
        previous &&
        ["READY", "STALE"].includes(previous.status)
      ) {
        return {
          ...previous,
          status: "STALE",
          limitations: [
            ...new Set([
              ...previous.limitations,
              ...incoming.limitations,
              "ACCOUNT_STALE",
            ]),
          ],
        };
      }
      return incoming;
    } catch (error) {
      if (error instanceof HttpError && [401, 403].includes(error.status)) {
        queryClient.setQueryData<AiAccount>(requestedKey, {
          status: error.status === 403 ? "RESTRICTED" : "UNAUTHENTICATED",
          provider: "codex",
          email: null,
          planType: null,
          authType: null,
          observedAt: null,
          windows: [],
          credits: null,
          limitations: [],
        });
      }
      throw error;
    }
  }
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => readAccount(queryKey, false, signal),
    enabled,
    retry: false,
    staleTime: 180_000,
    refetchInterval: enabled ? 180_000 : false,
  });
  const refresh = useMutation({
    mutationFn: async (requestedKey: typeof queryKey) => {
      await queryClient.cancelQueries({ queryKey: requestedKey, exact: true });
      return readAccount(requestedKey, true);
    },
    onSuccess: (account, requestedKey) =>
      queryClient.setQueryData(requestedKey, account),
  });
  const refreshBelongsToSession = refresh.variables?.[1] === queryKey[1];
  return {
    ...query,
    error:
      (refreshBelongsToSession && refresh.submittedAt >= query.dataUpdatedAt
        ? refresh.error
        : null) ?? query.error,
    isFetching:
      query.isFetching || (refreshBelongsToSession && refresh.isPending),
    refresh: () => refresh.mutate(queryKey),
  };
}

import type { DailyResearch } from "../../api/investment";
import { useCallback, useRef } from "react";

export function economicArchivePollInterval(
  daily?: DailyResearch | null,
  elapsedMs = 0,
  hasError = false,
): number | false {
  const economic = daily?.economicResearch;
  const currentId = daily?.pipeline
    ? daily.pipeline.researchRunId
    : daily?.runId;
  if (
    hasError ||
    economic?.version !== "economic-research-delivery-v1" ||
    economic.actionable !== false ||
    typeof economic.researchId !== "string" ||
    !economic.researchId ||
    economic.researchId !== currentId ||
    economic.archiveState === "ARCHIVED" ||
    economic.archiveState === "UNRECOVERABLE" ||
    (!economic.archivePending && economic.status !== "PENDING")
  )
    return false;
  return elapsedMs < 5 * 60_000 ? 60_000 : 120_000;
}

interface ArchiveQuery {
  queryHash?: string;
  state: {
    data?: unknown;
    error: unknown;
    errorUpdateCount: number;
    fetchStatus: "fetching" | "paused" | "idle";
  };
}
function contextDaily(query: ArchiveQuery) {
  const data = query.state.data;
  return data && typeof data === "object" && "dailyResearch" in data
    ? (data as { dailyResearch?: DailyResearch | null }).dailyResearch
    : undefined;
}

export function useEconomicArchivePolling() {
  const pending = useRef<{
    researchId: string;
    since: number;
    recoveries: number;
    lastFailure?: string;
    recoverAt?: number;
    reconnectChecked: boolean;
  } | null>(null);
  const cycleFor = useCallback((daily: DailyResearch) => {
    const researchId = daily.economicResearch!.researchId!;
    if (pending.current?.researchId !== researchId) {
      pending.current = {
        researchId,
        since: Date.now(),
        recoveries: 0,
        reconnectChecked: false,
      };
    }
    return pending.current;
  }, []);
  const interval = useCallback(
    (query: ArchiveQuery): number | false => {
      const daily = contextDaily(query);
      if (economicArchivePollInterval(daily, 0) === false) return false;
      const cycle = cycleFor(daily!);
      const now = Date.now();
      if (query.state.error) {
        // Native retries remain unchanged; only a final idle error spends a recovery opportunity.
        if (query.state.fetchStatus !== "idle") return false;
        const failure = `${query.queryHash ?? "context"}:${query.state.errorUpdateCount}`;
        if (cycle.lastFailure !== failure) {
          cycle.lastFailure = failure;
          cycle.recoverAt = undefined;
          if (cycle.recoveries < 3) {
            cycle.recoverAt = now + [15_000, 30_000, 60_000][cycle.recoveries];
            cycle.recoveries++;
          }
        }
        return cycle.recoverAt == null
          ? false
          : Math.max(1, cycle.recoverAt - now);
      }
      cycle.recoverAt = undefined;
      return economicArchivePollInterval(daily, Math.max(0, now - cycle.since));
    },
    [cycleFor],
  );
  const refetchOnReconnect = useCallback(
    (query: ArchiveQuery): boolean | "always" => {
      const daily = contextDaily(query);
      const economic = daily?.economicResearch;
      // Preserve the normal context-query reconnection policy outside archive waiting.
      if (!economic?.archivePending && economic?.status !== "PENDING")
        return true;
      if (economicArchivePollInterval(daily, 0) === false) return false;
      const cycle = cycleFor(daily!);
      if (cycle.reconnectChecked) return false;
      cycle.reconnectChecked = true;
      return "always";
    },
    [cycleFor],
  );
  return { refetchInterval: interval, refetchOnReconnect };
}

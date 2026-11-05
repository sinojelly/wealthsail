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
  return elapsedMs < 5 * 60_000 ? 15_000 : 60_000;
}

export function useEconomicArchivePolling() {
  const pending = useRef<{ researchId: string; since: number } | null>(null);
  return useCallback(
    (query: { state: { data?: unknown; error: unknown } }): number | false => {
      const data = query.state.data;
      const daily =
        data && typeof data === "object" && "dailyResearch" in data
          ? (data as { dailyResearch?: DailyResearch | null }).dailyResearch
          : undefined;
      if (
        economicArchivePollInterval(daily, 0, !!query.state.error) === false
      ) {
        pending.current = null;
        return false;
      }
      const researchId = daily!.economicResearch!.researchId!;
      const now = Date.now();
      if (pending.current?.researchId !== researchId)
        pending.current = { researchId, since: now };
      return economicArchivePollInterval(
        daily,
        Math.max(0, now - pending.current.since),
      );
    },
    [],
  );
}

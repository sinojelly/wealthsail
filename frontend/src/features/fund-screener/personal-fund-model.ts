import type {
  HeldFundWatch,
  StudyTrack,
  StudyTrackList,
} from "../../api/study-api";

export interface PersonalFundGroup {
  key: string;
  market: string;
  code?: string;
  label: string;
  holding?: HeldFundWatch;
  tracks: StudyTrack[];
  trackingSuppressed: boolean;
}

export function groupPersonalFunds(
  list: StudyTrackList,
  today: string,
): PersonalFundGroup[] {
  const groups = new Map<string, PersonalFundGroup>();
  const suppressed = new Set(list.suppressedFundKeys ?? []);
  for (const holding of list.heldFunds ?? []) {
    const code = holding.fundCode || undefined;
    const market = holding.fundMarket || "UNKNOWN";
    const instrumentKey = `${market}:instrument:${holding.instrumentId}`;
    const key = code ? `${market}:${code}` : instrumentKey;
    groups.set(key, {
      key,
      market,
      code,
      label: holding.label,
      holding,
      tracks: [],
      trackingSuppressed: suppressed.has(key) || suppressed.has(instrumentKey),
    });
  }
  for (const track of list.items) {
    if (
      !track.enabled ||
      track.status === "ENDED" ||
      (track.expiresOn && track.expiresOn < today)
    )
      continue;
    const plan = track.priceWatch?.frozenPlan;
    if (plan?.subjectKind === "index") continue;
    const explicitMarket = track.scope.fundMarket ?? plan?.market;
    const holding = [...groups.values()].find(
      (group) =>
        track.scope.instrumentId &&
        group.holding?.instrumentId === track.scope.instrumentId &&
        (!explicitMarket || explicitMarket === group.market),
    );
    const code =
      track.scope.fundCode ||
      plan?.fundCode ||
      (plan?.subjectKind === "nav" ? plan.subjectId : undefined);
    const instrumentId =
      track.scope.instrumentId ||
      (plan?.subjectKind === "fund" ? plan.subjectId : undefined);
    if (!code && !instrumentId) continue;
    const market = explicitMarket || holding?.market || "UNKNOWN";
    const key =
      holding?.key ??
      (code ? `${market}:${code}` : `${market}:instrument:${instrumentId}`);
    const instrumentKey = instrumentId
      ? `${market}:instrument:${instrumentId}`
      : undefined;
    const group = groups.get(key);
    if (
      group?.trackingSuppressed ||
      suppressed.has(key) ||
      (instrumentKey && suppressed.has(instrumentKey))
    )
      continue;
    const row: PersonalFundGroup = group ?? {
      key,
      market,
      code: code || undefined,
      label: plan?.label ?? track.title,
      tracks: [],
      trackingSuppressed: false,
    };
    if (!row.tracks.some((existing) => existing.id === track.id))
      row.tracks.push(track);
    groups.set(key, row);
  }
  return [...groups.values()];
}

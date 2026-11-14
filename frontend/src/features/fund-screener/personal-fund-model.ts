import type {
  HeldFundWatch,
  StudyPriceWatch,
  StudyTrack,
  StudyTrackList,
} from "../../api/study-api";
import type { SellRiskAlert } from "../../api/investment";
import type { PersonalFundReference } from "../../api/personal-funds";
import { fundOperationDistance } from "./fund-operation-distance";
export const relationshipLabels = {
  HELD: "已持仓",
  TRACKED: "已跟踪",
  WATCHLIST: "自选关注",
  RESEARCH_MENTIONED: "本期研究提及",
};

export interface PersonalFundGroup {
  key: string;
  market: string;
  code?: string;
  label: string;
  holding?: HeldFundWatch;
  tracks: StudyTrack[];
  trackingSuppressed: boolean;
  reference?: PersonalFundReference;
}

/** Display-only confidence: never infer a trigger from a different quote or plan. */
export function personalWatchState(watch?: StudyPriceWatch | null) {
  if (!watch) return undefined;
  if (watch.state === "HOLDING" && watch.holding?.state !== "CONFIRMED")
    return "UNKNOWN";
  const quote =
    watch.basis === "CONFIRMED_NAV"
      ? watch.confirmedNav
      : watch.basis === "ESTIMATE_1430"
        ? watch.estimate1430
        : watch.basis === "ESTIMATE_INTRADAY"
          ? watch.currentEstimate
          : undefined;
  const plan = watch.frozenPlan;
  const selected = plan?.tracks.find(
    (row) => row.track === (plan.selectedTrack ?? plan.track),
  );
  if (
    !selected ||
    !watch.asOf ||
    !quote?.asOf ||
    quote.value == null ||
    quote.usable !== true
  )
    return "UNKNOWN";
  return watch.state;
}

/** A code alone cannot establish either fund identity or the same risk observation. */
export function hasSameSellReminder(
  group: PersonalFundGroup,
  track: StudyTrack,
  alerts: readonly SellRiskAlert[],
) {
  const watch = track.priceWatch;
  const state = personalWatchState(watch);
  if (
    !watch ||
    !group.code ||
    (state !== "STOP_LOSS" && state !== "TAKE_PROFIT")
  )
    return false;
  const plan = watch.frozenPlan;
  const selected = plan?.tracks.find(
    (row) => row.track === (plan.selectedTrack ?? plan.track),
  );
  const quote =
    watch.basis === "CONFIRMED_NAV"
      ? watch.confirmedNav
      : watch.basis === "ESTIMATE_1430"
        ? watch.estimate1430
        : null;
  if (!selected || !quote) return false;
  return alerts.some(
    (alert) =>
      alert.planOrigin === "FROZEN_STUDY" &&
      alert.relationship.includes("TRACKED") &&
      !!alert.trackId &&
      alert.trackId === track.id &&
      alert.fundCode === group.code &&
      alert.selectedTrack === selected.track &&
      alert.basis === watch.basis &&
      !!alert.asOf &&
      alert.asOf === watch.asOf &&
      alert.asOf === quote.asOf &&
      alert.value != null &&
      alert.value === quote.value &&
      (state === "STOP_LOSS"
        ? alert.state === "STOP_LOSS" &&
          selected.stopLossPrice != null &&
          alert.stopLossPrice === selected.stopLossPrice
        : (alert.state === "TAKE_PROFIT_T1" ||
            alert.state === "TAKE_PROFIT_T2") &&
          selected.takeProfitT1 != null &&
          selected.takeProfitT2 != null &&
          alert.takeProfitT1 === selected.takeProfitT1 &&
          alert.takeProfitT2 === selected.takeProfitT2),
  );
}

export function groupPersonalFunds(
  list: StudyTrackList,
  today: string,
  references: readonly PersonalFundReference[] = [],
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
  for (const reference of references) {
    const group =
      groups.get(reference.key) ??
      [...groups.values()].find(
        (row) =>
          reference.instrumentId &&
          row.holding?.instrumentId === reference.instrumentId &&
          row.market === reference.fundMarket &&
          (!row.code || row.code === reference.fundCode),
      );
    if (group) {
      groups.delete(group.key);
      group.reference = reference;
      group.key = reference.key;
      group.code = reference.fundCode ?? group.code;
      groups.set(group.key, group);
    } else if (
      reference.relationships.some(
        (key) => key === "HELD" || key === "WATCHLIST" || key === "TRACKED",
      ) &&
      (!suppressed.has(reference.key) ||
        reference.relationships.includes("HELD") ||
        reference.relationships.includes("WATCHLIST"))
    )
      groups.set(reference.key, {
        key: reference.key,
        market: reference.fundMarket,
        code: reference.fundCode ?? undefined,
        label: reference.label,
        tracks: [],
        trackingSuppressed: suppressed.has(reference.key),
        reference,
        holding: reference.relationships.includes("HELD")
          ? {
              instrumentId: reference.instrumentId ?? "",
              fundCode: reference.fundCode,
              fundMarket: reference.fundMarket,
              label: reference.label,
              confirmedNav: reference.confirmedNav,
            }
          : undefined,
      });
  }
  return [...groups.values()];
}

export function personalFundPriority(group: PersonalFundGroup) {
  const plan =
    group.reference?.plan ??
    group.tracks.find((row) => row.priceWatch?.frozenPlan)?.priceWatch
      ?.frozenPlan;
  const quotes = [
    group.reference?.confirmedNav,
    group.holding?.confirmedNav,
    ...group.tracks.map((row) => row.priceWatch?.confirmedNav),
  ]
    .filter(
      (row) =>
        row?.value != null &&
        (!row.fundCode || row.fundCode === group.code) &&
        (!row.subjectId || row.subjectId === group.code),
    )
    .sort((a, b) => (b!.asOf ?? "").localeCompare(a!.asOf ?? ""));
  const samePlan =
    plan &&
    (!plan.market || plan.market === group.market) &&
    (!plan.fundCode || plan.fundCode === group.code);
  const position = fundOperationDistance(
    samePlan ? plan : null,
    quotes[0]?.value,
  );
  const nearBuy =
    !position.stopTriggered &&
    position.buyDistance != null &&
    position.buyDistance < 2;
  const nearSell = position.sellDistance != null && position.sellDistance < 2;
  const held =
    !!group.holding || !!group.reference?.relationships.includes("HELD");
  const rank =
    held && position.stopTriggered ? 0 : held && nearSell ? 1 : nearBuy ? 2 : 3;
  const distance =
    rank === 0
      ? 0
      : rank === 1
        ? position.sellDistance
        : rank === 2 || !held
          ? position.buyDistance
          : Math.min(
              position.buyDistance ?? Infinity,
              position.sellDistance ?? Infinity,
            );
  const label =
    rank === 0
      ? "止损参考触发"
      : rank === 1
        ? position.inSellZone
          ? "已入卖区"
          : `接近卖区（差 ${position.sellDistance!.toFixed(2)}%）`
        : position.stopTriggered
          ? "跌破止损参考"
          : position.inBuyZone
            ? "已入买区"
            : nearBuy
              ? `接近买区（差 ${position.buyDistance!.toFixed(2)}%）`
              : position.buyDistance != null
                ? `距买区 ${position.buyDistance.toFixed(2)}%`
                : "价格资料待补齐";
  return {
    ...position,
    rank,
    distance: distance ?? Infinity,
    label,
    defaultExpanded: rank < 3,
  };
}

export function sortPersonalFunds(groups: readonly PersonalFundGroup[]) {
  const priorities = new Map(
    groups.map((row) => [row, personalFundPriority(row)]),
  );
  return [...groups].sort((a, b) => {
    const x = priorities.get(a)!,
      y = priorities.get(b)!;
    return (
      x.rank - y.rank || x.distance - y.distance || a.key.localeCompare(b.key)
    );
  });
}

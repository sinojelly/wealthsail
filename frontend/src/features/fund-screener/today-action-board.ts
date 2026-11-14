import type {
  ActionableZoneResult,
  OpportunityContext,
  QuantitativeScanItem,
} from "../../api/investment";
import { fundOperationDistance } from "./fund-operation-distance";
import { fundDisplayName } from "./plan-display";

export interface TodayPriority {
  rank: number;
  distance: number;
  expanded: boolean;
  label: string;
  percentile: number;
  tone: "neutral" | "buy" | "near" | "stop" | "sell" | "trend";
}
export interface TodayFund {
  key: string;
  code: string;
  name: string;
  plans: ActionableZoneResult[];
  origins: string[];
  insights: Array<{ title: string; thesis: string }>;
  priority: TodayPriority;
}
export interface TodayInsight {
  key: string;
  title: string;
  thesis: string;
  kind: string;
  auditStatus?: string;
  funds: string[];
  priority: TodayPriority;
}
export interface TodayFundGroup {
  representative: TodayFund;
  alternatives: TodayFund[];
}

const DAY = 86_400_000;
const ENTRY_DISTANCE = 2;
const MIN_NAV_SAMPLES = 120;

function pendingNav(plan: ActionableZoneResult) {
  return (
    plan.priceBasis === "UNIT_NAV" &&
    Math.max(
      plan.series?.points.length ?? 0,
      plan.historicalPriceWindow?.sampleCount ?? 0,
      (plan as QuantitativeScanItem).historySampleCount ?? 0,
    ) < 60
  );
}

export function hasPendingTodayNav(context?: OpportunityContext) {
  const report = context?.dailyResearch?.report;
  if (!report) return false;
  return Boolean(
    report.discoveryActionableZones?.some(pendingNav) ||
    report.mentionedFunds?.some(
      ({ actionableZones }) => !actionableZones || pendingNav(actionableZones),
    ),
  );
}

// Prefer a known track over shared prose about several sectors. These
// aliases describe fund names, never the accompanying model narrative.
const namedTracks: ReadonlyArray<readonly [RegExp, string]> = [
  [/沪深300/i, "INDEX:CSI300"],
  [/中证A500/i, "INDEX:CSI_A500"],
  [/中证500/i, "INDEX:CSI500"],
  [/金融科技|金融IT/i, "FINTECH"],
  [/机器人|具身智能/i, "ROBOT"],
  [/军工|国防|航空航天|卫星产业|卫星通信|商业航天/i, "AEROSPACE"],
  [/医药|医疗|创新药|生物科技|生物医药/i, "HEALTHCARE"],
  [/食品饮料|白酒/i, "FOOD_BEVERAGE"],
  [/新能源车|汽车|智能驾驶/i, "AUTOMOTIVE"],
  [/银行|银行红利/i, "BANK"],
  [/券商|证券公司/i, "BROKER"],
  [/煤炭|煤业/i, "COAL"],
];
const trackAliases: Record<string, string> = {
  DEFENCE: "AEROSPACE",
  COMMERCIAL_SPACE: "AEROSPACE",
  EVENT_COMMERCIAL_SPACE: "AEROSPACE",
  INNOVATIVE_DRUG: "HEALTHCARE",
  MEDICAL: "HEALTHCARE",
  PHARMA: "HEALTHCARE",
  MEDICINE: "HEALTHCARE",
};

function fundRegion(name: string) {
  if (/恒生|港股/.test(name)) return "HK";
  if (/纳斯达克|纳指|标普|美股/.test(name)) return "US";
  return /QDII|海外|全球|跨境/i.test(name) ? "OVERSEAS" : "CN";
}

function navDay(value: string | null | undefined) {
  const day = value?.slice(0, 10);
  if (!day || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const date = Date.parse(`${day}T00:00:00Z`);
  return Number.isFinite(date) &&
    new Date(date).toISOString().slice(0, 10) === day
    ? date
    : null;
}

function navCoverage(plan: ActionableZoneResult, code: string) {
  const historyQuality = (plan as Partial<QuantitativeScanItem>).historyQuality;
  const series = plan.series;
  const window = plan.historicalPriceWindow;
  const asOf = navDay(plan.asOf);
  const seriesReady =
    series?.state === "READY" &&
    series.points.length >= MIN_NAV_SAMPLES &&
    series.fundCode === code &&
    series.priceBasis === "UNIT_NAV" &&
    !!series.sourceUri &&
    navDay(series.asOf) === asOf;
  const windowReady =
    window?.state === "READY" &&
    window.sampleCount >= Math.max(window.requiredSamples, MIN_NAV_SAMPLES) &&
    !!plan.sourceUri &&
    plan.priceRefresh?.state === "READY" &&
    navDay(plan.priceRefresh.navAsOf) === asOf;
  const start = navDay(
    windowReady ? window.start : seriesReady ? series.points[0]?.date : null,
  );
  const end = navDay(
    windowReady ? window.end : seriesReady ? series.points.at(-1)?.date : null,
  );
  const verified =
    asOf !== null &&
    start !== null &&
    end === asOf &&
    start <= end &&
    (!plan.priceRefresh || plan.priceRefresh.state === "READY") &&
    (!historyQuality || historyQuality === "READY") &&
    !plan.priceRefresh?.reason;
  return { asOf, verified, days: verified ? (end - start) / DAY : 0 };
}

function referenceReady(plan: ActionableZoneResult) {
  const track = fundOperationDistance(plan).track;
  return (
    track?.priceState === "READY" &&
    !track.entryBlocked &&
    !plan.entryBlocked &&
    !plan.subscriptionRestricted &&
    !plan.subscriptionStatus?.restricted &&
    plan.subscriptionStatus?.purchaseStatus !== "SUSPENDED"
  );
}
const waiting = (): TodayPriority => ({
  rank: 4,
  distance: Infinity,
  expanded: false,
  tone: "neutral",
  label: "净值资料待补齐",
  percentile: Infinity,
});
const observation = (): TodayPriority => ({ ...waiting(), label: "主题观察" });

function shareFamily(fund: TodayFund) {
  const plan = fund.plans[0];
  if (
    plan.priceBasis !== "UNIT_NAV" ||
    (plan.market ?? "FUND_CN") !== "FUND_CN"
  )
    return null;
  // Match a full index fund product name, including provider and product form.
  // A common index or an A/C suffix alone does not make two funds share classes.
  const matched = fundDisplayName(fund.name, fund.code)
    .replace(/\s+/g, "")
    .match(
      /^(.+(?:指数|ETF|联接)(?:基金|发起式)*)([AC])(?:类|份额)?(?:[（(]人民币[)）]|人民币)?$/i,
    );
  return matched?.[1].toUpperCase() ?? null;
}
export function todayPriority(
  plan: ActionableZoneResult,
  held = false,
): TodayPriority {
  const p = fundOperationDistance(plan);
  if (p.current == null) return waiting();
  const percentile = p.percentile ?? Infinity;
  const base = { percentile, expanded: false };
  if (p.stopTriggered)
    return {
      ...base,
      rank: held ? 2 : 4,
      distance: 0,
      label: "⚠️ 跌破止损线（深跌观察）",
      tone: "stop",
    };
  if (p.inSellZone)
    return {
      ...base,
      rank: held ? 2 : 4,
      distance: 0,
      label: held ? "持仓止盈提醒" : "已达卖出参考区",
      tone: "sell",
    };
  if (held && p.sellDistance != null && p.sellDistance < 2)
    return {
      ...base,
      rank: 2,
      distance: p.sellDistance,
      label: `持仓距卖区 ${p.sellDistance.toFixed(2)}%`,
      tone: "sell",
    };
  if (p.inBuyZone)
    return { ...base, rank: 0, distance: 0, label: "已入买区", tone: "buy" };
  if (p.buyDistance != null && p.buyDistance < ENTRY_DISTANCE)
    return {
      ...base,
      rank: 1,
      distance: p.buyDistance,
      label: `距买区 ${p.buyDistance.toFixed(2)}%`,
      tone: "near",
    };
  if (hasTodayRightTrend(plan))
    return {
      ...base,
      rank: 2,
      distance: p.buyDistance ?? Infinity,
      label:
        p.buyDistance == null
          ? "右侧趋势机会"
          : `右侧趋势观察 · 距买区 ${p.buyDistance.toFixed(2)}%`,
      tone: "trend",
    };
  if (p.buyDistance === null) return waiting();
  return {
    ...base,
    rank: 3,
    distance: p.buyDistance ?? Infinity,
    label: `距买区 ${p.buyDistance.toFixed(2)}%`,
    tone: "neutral",
  };
}
export function hasTodayRightTrend(plan: ActionableZoneResult) {
  return (
    plan.tracks?.some(
      (track) => track.track === "RIGHT_TREND" && track.state === "CANDIDATE",
    ) ||
    (plan as QuantitativeScanItem).signals?.some(
      (signal) =>
        signal.track === "RIGHT_TREND" && signal.triggerState === "CANDIDATE",
    ) ||
    false
  );
}
export function compareTodayPriority(a: TodayPriority, b: TodayPriority) {
  return (
    a.rank - b.rank ||
    (a.rank === 0 ? a.percentile - b.percentile : a.distance - b.distance) ||
    0
  );
}

/** One visible price board per identity; preserve distinct plans as alternatives. */
export function buildTodayBoard(
  data: OpportunityContext,
  watchedCodes: readonly string[] = [],
) {
  const daily = data.dailyResearch;
  const report = daily?.report;
  const directions = report?.valueDirections?.length
    ? report.valueDirections
    : (daily?.economicResearch?.observations.valueDirections ?? []);
  const topics = report?.researchTopics?.length
    ? report.researchTopics
    : (report?.topicCatalog ?? []);
  const mentions =
    report?.mentionedFunds ??
    daily?.economicResearch?.observations.mentionedFunds ??
    [];
  const personal = [
    ...(data.sellRiskAlerts?.items ?? []),
    ...(data.sellRiskAlerts?.actionReminders ?? []),
    ...(data.sellRiskAlerts?.normalHoldings ?? []),
  ];
  const held = new Set(
    personal
      .filter((r) => r.relationship.includes("HELD"))
      .map((r) => r.fundCode),
  );
  const watched = new Set([
    ...watchedCodes,
    ...personal
      .filter((r) => r.relationship.includes("TRACKED"))
      .map((r) => r.fundCode),
  ]);
  const observations = new Map(
    (daily?.fundObservation?.items ?? [])
      .filter((row) => row.identityVerified)
      .map((row) => [row.fundCode, row]),
  );
  const sectors = new Map(
    (report?.quantitativeScan?.items ?? []).map((row) => [
      row.fundCode ?? row.subjectId,
      row.discoverySector,
    ]),
  );
  const funds = new Map<string, TodayFund>();
  const signatures = new Map<string, Map<string, number>>();
  const insights: TodayInsight[] = [];
  const directionsByKey = new Map<string, { title: string; thesis: string }>();
  function add(plan: ActionableZoneResult, origin: string, name?: string) {
    const code = plan.fundCode ?? plan.subjectId;
    const unit = [
      "CNY",
      "元",
      "元/份",
      "CNY/unit",
      "CNY/份",
      "CNY/share",
    ].includes(plan.unit ?? "")
      ? "CNY/份"
      : (plan.unit ?? "");
    const key = `${plan.market ?? "FUND_CN"}:${code}:${plan.priceBasis}:${unit}`;
    let fund = funds.get(key);
    if (!fund) {
      fund = {
        key,
        code,
        name: name ?? plan.label,
        plans: [],
        origins: [],
        insights: [],
        priority: waiting(),
      };
      funds.set(key, fund);
      signatures.set(key, new Map());
    }
    const signature = JSON.stringify([
      plan.asOf,
      plan.currentPrice,
      plan.methodVersion,
      plan.selectedTrack,
      plan.track,
      plan.tracks,
    ]);
    const recorded = signatures.get(key)!;
    const previous = recorded.get(signature);
    if (previous === undefined) {
      recorded.set(signature, fund.plans.length);
      fund.plans.push(plan);
    } else {
      const old = fund.plans[previous];
      const oldNav = navCoverage(old, code);
      const newNav = navCoverage(plan, code);
      const richer =
        (Number(newNav.verified) - Number(oldNav.verified) ||
          newNav.days - oldNav.days ||
          (plan.series?.points.length ?? 0) -
            (old.series?.points.length ?? 0)) > 0
          ? plan
          : old;
      const macroOpportunity = plan.macroOpportunity ?? old.macroOpportunity;
      fund.plans[previous] =
        macroOpportunity === richer.macroOpportunity
          ? richer
          : { ...richer, macroOpportunity };
    }
    if (!fund.origins.includes(origin)) fund.origins.push(origin);
    return key;
  }
  for (const [kind, rows] of [
    ["价值方向", directions],
    ["研究主题", topics],
  ] as const) {
    rows.forEach((row, index) => {
      const title = row.title ?? `${kind}${index + 1}`;
      const plans =
        "plans" in row && Array.isArray(row.plans)
          ? (row.plans as ActionableZoneResult[])
          : "fundPlans" in row && Array.isArray(row.fundPlans)
            ? (row.fundPlans as ActionableZoneResult[])
            : [];
      const primary =
        "primaryFundCodes" in row && Array.isArray(row.primaryFundCodes)
          ? new Set(row.primaryFundCodes)
          : null;
      const associated = plans.map((p) => {
        const inScope = !primary || primary.has(p.fundCode ?? p.subjectId);
        const key = add(p, inScope ? title : "其他研究提及");
        return inScope ? key : null;
      });
      const thesis =
        row.thesis ?? ("summary" in row ? String(row.summary ?? "") : "");
      const associatedFunds = [
        ...new Set(associated.filter((key) => key !== null)),
      ];
      if (thesis) {
        const insight = { title, thesis };
        if (row.key && kind === "价值方向")
          directionsByKey.set(row.key, insight);
        for (const key of associatedFunds)
          funds.get(key)!.insights.push(insight);
      }
      insights.push({
        key: `${kind}:${row.key ?? index}`,
        title,
        thesis,
        kind,
        auditStatus:
          "auditStatus" in row ? (row.auditStatus as string) : undefined,
        funds: associatedFunds,
        priority: observation(),
      });
    });
  }
  const unpriced: typeof mentions = [];
  mentions.forEach((m) => {
    if (m.actionableZones) {
      const key = add(m.actionableZones, "正文提及", m.name ?? m.label);
      const thesis = m.directionKey && directionsByKey.get(m.directionKey);
      const fund = funds.get(key)!;
      if (thesis && !fund.insights.includes(thesis)) fund.insights.push(thesis);
    } else if (!unpriced.some((row) => row.fundCode === m.fundCode))
      unpriced.push(m);
  });
  (report?.discoveryActionableZones ?? []).forEach((p) => add(p, "代表基金池"));
  (report?.quantitativeScan?.items ?? []).forEach((p) => add(p, "量化扫描"));
  const ordered = [...funds.values()];
  ordered.forEach((f) => {
    f.plans.sort(
      (a, b) =>
        Number(fundOperationDistance(b).current != null) -
          Number(fundOperationDistance(a).current != null) ||
        (b.asOf ?? "").localeCompare(a.asOf ?? "") ||
        Number(navCoverage(b, f.code).verified) -
          Number(navCoverage(a, f.code).verified) ||
        compareTodayPriority(
          todayPriority(a, held.has(f.code)),
          todayPriority(b, held.has(f.code)),
        ),
    );
    const missingMacro = f.plans.find(
      (plan) => plan.macroOpportunity,
    )?.macroOpportunity;
    if (!f.plans[0].macroOpportunity && missingMacro)
      f.plans[0] = { ...f.plans[0], macroOpportunity: missingMacro };
    f.priority = todayPriority(f.plans[0], held.has(f.code));
  });
  const personalOrder = (fund: TodayFund) =>
    held.has(fund.code) ? 0 : watched.has(fund.code) ? 1 : 2;
  const tailwindWithoutRisk = (fund: TodayFund) => {
    const macro = fund.plans[0].macroOpportunity;
    return Number(macro?.stance === "TAILWIND" && macro.riskLevel === "NONE");
  };
  const compareFunds = (a: TodayFund, b: TodayFund) =>
    (a.priority.rank === 0 && b.priority.rank === 0
      ? Number(personalOrder(a) > 1) - Number(personalOrder(b) > 1) ||
        tailwindWithoutRisk(b) - tailwindWithoutRisk(a)
      : 0) ||
    compareTodayPriority(a.priority, b.priority) ||
    a.code.localeCompare(b.code);
  ordered.sort(compareFunds);
  const coverage = new Map(
    ordered.map((fund) => [fund, navCoverage(fund.plans[0], fund.code)]),
  );
  const latestNav = ordered.reduce((latest, fund) => {
    const nav = coverage.get(fund)!;
    // Omit overseas calendars from the comparison with same-market funds.
    return nav.verified && fundRegion(fund.name) === "CN"
      ? Math.max(latest, nav.asOf!)
      : latest;
  }, 0);
  function canExpand(fund: TodayFund) {
    const nav = coverage.get(fund)!;
    const observation = observations.get(fund.code);
    const overseas = fundRegion(fund.name) !== "CN";
    const observationDate = navDay(observation?.confirmedNav?.asOf);
    return (
      nav.days >= 365 &&
      (overseas || nav.asOf === latestNav) &&
      (observationDate === null || nav.asOf! >= observationDate) &&
      (observation?.observationStatus !== "STALE" ||
        (observationDate !== null && nav.asOf! > observationDate)) &&
      observation?.subscription?.status !== "SUSPENDED" &&
      observation?.subscription?.restricted !== true &&
      referenceReady(fund.plans[0]) &&
      fund.priority.rank <= 1 &&
      fund.priority.distance < ENTRY_DISTANCE
    );
  }
  const eligible = new Set(ordered.filter(canExpand));
  const trackGroups = new Map<string, TodayFund[]>();
  for (const fund of ordered) {
    const plan = fund.plans[0] as QuantitativeScanItem;
    const knownSectors =
      observations.get(fund.code)?.industry?.industryIds ?? [];
    const sector =
      sectors.get(fund.code) ??
      plan.discoverySector ??
      (knownSectors.length === 1 ? knownSectors[0] : undefined);
    const name = fundDisplayName(fund.name, fund.code);
    const track =
      plan.priceBasis === "UNIT_NAV"
        ? (namedTracks.find(([pattern]) => pattern.test(name))?.[1] ??
          (sector && sector !== "BROAD"
            ? Object.hasOwn(trackAliases, sector)
              ? trackAliases[sector]
              : sector
            : null))
        : null;
    const family = shareFamily(fund);
    const group = track ?? (family ? `PRODUCT:${family}` : fund.key);
    const region = fundRegion(name);
    const key = `${plan.market ?? "FUND_CN"}:${plan.priceBasis}:${fund.key.split(":").at(-1)}:${region}:${group}`;
    const members = trackGroups.get(key) ?? [];
    members.push(fund);
    trackGroups.set(key, members);
  }
  const subscriptionOrder = (fund: TodayFund) => {
    const status =
      observations.get(fund.code)?.subscription?.status ??
      fund.plans[0].subscriptionStatus?.purchaseStatus;
    return status === "OPEN" ? 0 : status === "SUSPENDED" ? 2 : 1;
  };
  const groups: TodayFundGroup[] = [...trackGroups.values()].map((members) => {
    const [representative, ...alternatives] = members.sort(
      (a, b) =>
        Number(eligible.has(b)) - Number(eligible.has(a)) ||
        personalOrder(a) - personalOrder(b) ||
        Number(coverage.get(b)!.days >= 729) -
          Number(coverage.get(a)!.days >= 729) ||
        (coverage.get(b)!.asOf ?? 0) - (coverage.get(a)!.asOf ?? 0) ||
        subscriptionOrder(a) - subscriptionOrder(b) ||
        compareFunds(a, b),
    );
    return { representative, alternatives };
  });
  groups.sort(
    (a, b) =>
      Number(eligible.has(b.representative)) -
        Number(eligible.has(a.representative)) ||
      compareFunds(a.representative, b.representative),
  );
  for (const { representative } of groups) {
    representative.priority.expanded = eligible.has(representative);
  }
  insights.forEach((i) => {
    i.funds.sort((a, b) =>
      compareTodayPriority(funds.get(a)!.priority, funds.get(b)!.priority),
    );
    const primary = i.funds.map((key) => funds.get(key)!);
    const sell = primary.find(
      (fund) => fundOperationDistance(fund.plans[0]).inSellZone,
    );
    const exitThesis = /卖区|止盈/.test(i.title);
    i.priority =
      sell || exitThesis
        ? {
            ...(sell?.priority ?? observation()),
            label: "已入卖区（止盈评估）",
            expanded: false,
            rank: 2,
          }
        : (primary[0]?.priority ?? observation());
  });
  insights.sort((a, b) => compareTodayPriority(a.priority, b.priority));
  const missing = unpriced.filter((m) => {
    const represented = ordered.filter(
      (f) =>
        f.code === m.fundCode &&
        f.plans[0].priceBasis === "UNIT_NAV" &&
        (f.plans[0].market ?? "FUND_CN") === "FUND_CN",
    );
    represented.forEach((f) => {
      if (!f.origins.includes("正文提及")) f.origins.push("正文提及");
      const thesis = m.directionKey && directionsByKey.get(m.directionKey);
      if (thesis && !f.insights.includes(thesis)) f.insights.push(thesis);
    });
    return !represented.length;
  });
  const holdingAlerts = ordered.filter(
    (fund) =>
      held.has(fund.code) &&
      (fund.priority.tone === "stop" || fund.priority.tone === "sell"),
  );
  return { funds: ordered, groups, insights, unpriced: missing, holdingAlerts };
}

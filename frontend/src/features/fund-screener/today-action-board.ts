import type {
  ActionableZoneResult,
  OpportunityContext,
} from "../../api/investment";
import { fundOperationDistance } from "./fund-operation-distance";

export interface TodayPriority {
  rank: number;
  distance: number;
  expanded: boolean;
  label: string;
  percentile: number;
}
export interface TodayFund {
  key: string;
  code: string;
  name: string;
  plans: ActionableZoneResult[];
  origins: string[];
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
const waiting = (): TodayPriority => ({
  rank: 4,
  distance: Infinity,
  expanded: false,
  label: "净值资料待补齐",
  percentile: Infinity,
});
export function todayPriority(
  plan: ActionableZoneResult,
  held = false,
): TodayPriority {
  const p = fundOperationDistance(plan);
  if (
    p.current == null ||
    p.low == null ||
    p.high == null ||
    p.low > p.high ||
    (plan.priceRefresh && plan.priceRefresh.state !== "READY")
  )
    return waiting();
  const percentile = p.percentile ?? Infinity;
  const base = { percentile, expanded: false };
  if (p.stopTriggered)
    return {
      ...base,
      rank: held ? 2 : 4,
      distance: 0,
      expanded: held,
      label: held ? "持仓止损提醒" : "跌破止损参考",
    };
  if (p.inSellZone)
    return {
      ...base,
      rank: held ? 2 : 4,
      distance: 0,
      expanded: held,
      label: held ? "持仓止盈提醒" : "已达卖出参考区",
    };
  if (p.inBuyZone)
    return { ...base, rank: 0, distance: 0, expanded: true, label: "已入买区" };
  if (p.buyDistance != null && p.buyDistance < 2)
    return {
      ...base,
      rank: 1,
      distance: p.buyDistance,
      expanded: true,
      label: `距买区 ${p.buyDistance.toFixed(2)}%`,
    };
  if (held && p.sellDistance != null && p.sellDistance < 2)
    return {
      ...base,
      rank: 2,
      distance: p.sellDistance,
      expanded: true,
      label: `持仓距卖区 ${p.sellDistance.toFixed(2)}%`,
    };
  if (
    plan.tracks?.some(
      (t) => t.track === "RIGHT_TREND" && t.state === "CANDIDATE",
    )
  )
    return {
      ...base,
      rank: 2,
      distance: p.buyDistance ?? Infinity,
      expanded: true,
      label: "右侧趋势机会",
    };
  return {
    ...base,
    rank: 3,
    distance: p.buyDistance ?? Infinity,
    label:
      p.buyDistance == null
        ? "观察机会"
        : `距买区 ${p.buyDistance.toFixed(2)}%`,
  };
}
export function compareTodayPriority(a: TodayPriority, b: TodayPriority) {
  return (
    a.rank - b.rank ||
    (a.rank === 0 ? a.percentile - b.percentile : a.distance - b.distance) ||
    0
  );
}

/** One visible price board per identity; preserve distinct plans as alternatives. */
export function buildTodayBoard(data: OpportunityContext) {
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
  const held = new Set(
    [
      ...(data.sellRiskAlerts?.items ?? []),
      ...(data.sellRiskAlerts?.actionReminders ?? []),
      ...(data.sellRiskAlerts?.normalHoldings ?? []),
    ]
      .filter((r) => r.relationship.includes("HELD"))
      .map((r) => r.fundCode),
  );
  const funds = new Map<string, TodayFund>();
  const signatures = new Map<string, Set<string>>();
  const insights: TodayInsight[] = [];
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
        priority: waiting(),
      };
      funds.set(key, fund);
      signatures.set(key, new Set());
    }
    const signature = JSON.stringify([
      plan.asOf,
      plan.currentPrice,
      plan.methodVersion,
      plan.selectedTrack,
      plan.track,
      plan.tracks,
    ]);
    if (!signatures.get(key)!.has(signature)) {
      fund.plans.push(plan);
      signatures.get(key)!.add(signature);
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
      insights.push({
        key: `${kind}:${row.key ?? index}`,
        title,
        thesis:
          row.thesis ?? ("summary" in row ? String(row.summary ?? "") : ""),
        kind,
        auditStatus:
          "auditStatus" in row ? (row.auditStatus as string) : undefined,
        funds: [...new Set(plans.map((p) => add(p, title)))],
        priority: waiting(),
      });
    });
  }
  const unpriced: typeof mentions = [];
  mentions.forEach((m) => {
    if (m.actionableZones)
      add(m.actionableZones, "正文提及", m.name ?? m.label);
    else if (!unpriced.some((row) => row.fundCode === m.fundCode))
      unpriced.push(m);
  });
  // Preserve the complete scan in compact form. Only actionable prices/trends
  // open automatically; the user can inspect every other original opportunity.
  (report?.quantitativeScan?.items ?? []).forEach((p) => add(p, "量化扫描"));
  const ordered = [...funds.values()];
  ordered.forEach((f) => {
    f.plans.sort(
      (a, b) =>
        (b.asOf ?? "").localeCompare(a.asOf ?? "") ||
        compareTodayPriority(
          todayPriority(a, held.has(f.code)),
          todayPriority(b, held.has(f.code)),
        ),
    );
    f.priority = todayPriority(f.plans[0], held.has(f.code));
  });
  ordered.sort(
    (a, b) =>
      compareTodayPriority(a.priority, b.priority) ||
      a.code.localeCompare(b.code),
  );
  insights.forEach((i) => {
    i.funds.sort((a, b) =>
      compareTodayPriority(funds.get(a)!.priority, funds.get(b)!.priority),
    );
    i.priority = i.funds.length ? funds.get(i.funds[0])!.priority : waiting();
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
    });
    return !represented.length;
  });
  return { funds: ordered, insights, unpriced: missing };
}

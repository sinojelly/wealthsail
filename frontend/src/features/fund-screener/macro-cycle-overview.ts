import type { OpportunityContext } from "../../api/investment";
import type { MacroCycleInsight } from "../../api/macro-cycle";
import { researchSummary } from "./research-reading";

export type CycleView = MacroCycleInsight & { origin?: string };

export function cycleReading(value: string) {
  return value
    .split(/(?<=[。；;])/)
    .filter(
      (clause) =>
        !/BLS.{0,12}(403|抓取)|Polymarket.{0,10}(超时|抓取)|网页未打开|\bRETRIEVAL_ONLY\b|^\s*[^。]*倾向属于官员观点[。；;]?$/.test(
          clause,
        ),
    )
    .join("")
    .trim();
}

function insight(value: unknown): CycleView | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as CycleView;
  return [row.stage, row.summary, row.title, row.driver, row.action].some(
    (text) => typeof text === "string" && text.trim(),
  )
    ? row
    : null;
}

const stages: Record<string, string> = {
  EARNINGS_DELIVERY: "业绩兑现",
  POLICY_CATALYST: "政策/事件催化",
  EARLY_COMMERCIALIZATION: "商业化早期",
  UNKNOWN: "观察中",
};

/** A reading projection only: no synthetic rates, odds or cycle conclusions. */
export function buildCycleOverview(data: OpportunityContext) {
  const report = data.dailyResearch?.report;
  const cycle = report?.cycleInsights;
  const coverage = (topic: string): CycleView | null => {
    const row = report?.coverage?.find(
      (item) =>
        item.topic === topic && item.status === "FOUND" && item.summary?.trim(),
    );
    return row
      ? {
          summary: row.summary,
          origin: "原报告观察",
          evidenceKind: "MODEL_ASSESSMENT",
        }
      : null;
  };
  const explicitIndustries = Array.isArray(cycle?.industries)
    ? cycle.industries.map(insight).filter((row): row is CycleView => !!row)
    : [];
  const industries: CycleView[] = explicitIndustries.length
    ? explicitIndustries
    : (report?.valueDirections ?? []).map((direction) => ({
        label: direction.title,
        stage: direction.industryCycle
          ? (stages[direction.industryCycle.stage] ??
            direction.industryCycle.label ??
            "观察中")
          : "观察中",
        driver:
          direction.industryCycle?.stage !== "UNKNOWN"
            ? direction.industryCycle?.evidenceSummary || direction.thesis
            : direction.thesis,
        sourceIds: direction.industryCycle?.sourceIds ?? direction.sourceIds,
        evidenceKind: "MODEL_ASSESSMENT",
        origin: "原报告方向",
      }));
  const factors = Array.isArray(cycle?.keyFactors)
    ? cycle.keyFactors.map(insight).filter((row): row is CycleView => !!row)
    : (report?.events ?? [])
        .filter(
          (event) => event.importance === "HIGH" && event.phase !== "PLANNED",
        )
        .map((event): CycleView => ({
          title: event.title,
          summary: event.summary,
          sourceIds: event.sourceIds,
          evidenceKind: "MODEL_ASSESSMENT",
          origin: "原报告重要事件",
        }));
  const full = researchSummary(
    report?.macroCycle ||
      report?.summary ||
      "综合研究尚未生成，以下为实际已采集观察。",
    Infinity,
  );
  return {
    global: insight(cycle?.globalLiquidity) ?? coverage("US_RATES"),
    domestic: insight(cycle?.domesticCycle) ?? coverage("DOMESTIC_GROWTH"),
    market:
      insight(cycle?.marketRegime) ??
      ({
        summary: full,
        origin: "原报告总纲",
        evidenceKind: "MODEL_ASSESSMENT",
      } as CycleView),
    factors,
    industries,
  };
}

const factDefinitions = [
  ["PMI", "制造业PMI", ""],
  ["CPI_YOY", "CPI同比", "%"],
  ["PPI_YOY", "PPI同比", "%"],
  ["M1_M2_GAP", "M1−M2剪刀差", "个百分点"],
  ["CN_T10", "中国10年国债", "%"],
  ["US_T10_REAL", "美国10年实际利率", "%"],
  ["M1_YOY", "M1同比", "%"],
  ["M2_YOY", "M2同比", "%"],
  ["TSF_STOCK_YOY", "社融存量同比", "%"],
  ["US_CPI_YOY", "美国CPI同比", "%"],
  ["US_CPI_MOM", "美国CPI环比", "%"],
  ["US_CORE_CPI_YOY", "美国核心CPI同比", "%"],
  ["US_PPI_YOY", "美国PPI同比", "%"],
  ["US_FED_FUNDS", "联邦基金有效利率", "%"],
  ["US_T2_NOMINAL", "美国2年国债", "%"],
  ["US_T10_NOMINAL", "美国10年名义利率", "%"],
  ["US_BRENT", "布伦特原油", "美元/桶"],
] as const;

export function cycleReportCutoff(data: OpportunityContext) {
  const daily = data.dailyResearch;
  return daily?.report?.asOf ?? daily?.reportCompletedAt ?? daily?.asOf ?? null;
}

function knownBy(value: string | null | undefined, cutoff?: string | null) {
  if (!value) return false;
  if (!cutoff) return true;
  if (value.length === 10 || cutoff.length === 10)
    return value.slice(0, 10) <= cutoff.slice(0, 10);
  return Date.parse(value) <= Date.parse(cutoff);
}

function validCycleSample(row: {
  value: unknown;
  state?: string;
  valueVerification?: string;
}) {
  return (
    row.value != null &&
    String(row.value).trim() !== "" &&
    Number.isFinite(Number(row.value)) &&
    ![
      "CONFLICT",
      "UNAVAILABLE",
      "NOT_COLLECTED",
      "PERMISSION_REQUIRED",
      "PENDING",
    ].includes(row.state ?? "") &&
    !["CONFLICT", "UNAVAILABLE", "PENDING"].includes(
      row.valueVerification ?? "",
    )
  );
}

export function cycleFacts(data: OpportunityContext, cutoff?: string | null) {
  const observations = [
    ...data.items,
    ...(data.series ?? []),
    ...(data.alphaPublic?.observations ?? []),
    ...(data.series ?? []).flatMap((series) =>
      (series.history ?? []).map((row) => ({
        ...row,
        code: series.code,
        asOf: row.date,
      })),
    ),
    ...data.items.flatMap((item) =>
      (item.history ?? []).map((row) => ({ ...row, code: item.code })),
    ),
  ];
  return factDefinitions.flatMap(([code, label, unit]) => {
    const item = observations
      .filter(
        (row) =>
          row.code === code &&
          validCycleSample(row) &&
          (!cutoff ||
            (knownBy(
              row.asOf ?? ("referenceDate" in row ? row.referenceDate : null),
              cutoff,
            ) &&
              knownBy(
                ("availableAt" in row ? row.availableAt : null) ??
                  row.publishedAt ??
                  ("retrievedAt" in row ? row.retrievedAt : null),
                cutoff,
              ))),
      )
      .sort((a, b) =>
        String(
          b.asOf ?? ("referenceDate" in b ? b.referenceDate : ""),
        ).localeCompare(
          String(a.asOf ?? ("referenceDate" in a ? a.referenceDate : "")),
        ),
      )[0];
    return item
      ? [
          {
            code,
            label,
            unit,
            value: Number(item.value).toFixed(code === "PMI" ? 1 : 2),
            asOf:
              item.asOf ??
              ("referenceDate" in item ? item.referenceDate : null),
            referencePeriod:
              "referencePeriod" in item ? item.referencePeriod : null,
            sourceUri: item.sourceUri,
          },
        ]
      : [];
  });
}

export type CycleFact = ReturnType<typeof cycleFacts>[number];

export function cycleTrend(
  data: OpportunityContext,
  code: string,
  cutoff?: string | null,
  supplemental?: OpportunityContext,
) {
  const series = data.series?.find((row) => row.code === code);
  const monthly = data.items.find((row) => row.code === code);
  const history = [
    ...(series?.history ?? []),
    ...(monthly?.history ?? []).map((row) => ({
      ...row,
      date: row.asOf,
      availableAt: row.publishedAt ?? row.retrievedAt,
      definitionKey: undefined,
      definitionVersion: undefined,
      metadata: undefined,
    })),
  ];
  const usable = new Map<
    string,
    { date: string; value: number; basis: string }
  >();
  if (supplemental && cutoff) {
    for (const point of cycleTrend(supplemental, code, cutoff)) {
      usable.set(point.date.slice(0, 7), point);
    }
  }
  const frozenMonths = new Set<string>();
  for (const row of history) {
    if (
      !validCycleSample(row) ||
      !knownBy(row.date, cutoff) ||
      (cutoff &&
        !knownBy(row.availableAt ?? row.publishedAt ?? row.retrievedAt, cutoff))
    )
      continue;
    const month = row.date.slice(0, 7);
    if (
      !/^\d{4}-(0[1-9]|1[0-2])$/.test(month) ||
      (frozenMonths.has(month) && (usable.get(month)?.date ?? "") > row.date)
    )
      continue;
    frozenMonths.add(month);
    usable.set(month, {
      date: row.date,
      value: Number(row.value),
      basis:
        row.definitionVersion ??
        row.metadata?.definitionVersion ??
        row.definitionKey ??
        row.metadata?.definitionKey ??
        "",
    });
  }
  return [...usable.values()]
    .sort((a, b) => a.date.localeCompare(b.date))
    .slice(-36);
}

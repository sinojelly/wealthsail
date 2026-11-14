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
    cycle?.displayMacroCycle ??
      (report?.macroCycle ||
        report?.summary ||
        "综合研究尚未生成，以下为实际已采集观察。"),
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
  ["DR007", "DR007", "%"],
  ["SHIBOR_7D", "7 天 Shibor", "%"],
  ["VIX", "VIX", "点"],
] as const;

export function cycleReportCutoff(data: OpportunityContext) {
  const daily = data.dailyResearch;
  return daily?.report?.asOf ?? daily?.reportCompletedAt ?? daily?.asOf ?? null;
}

function knownBy(value: string | null | undefined, cutoff?: string | null) {
  if (!value) return false;
  if (!cutoff) return true;
  if (/^\d{4}-\d{2}$/.test(value)) return value <= cutoff.slice(0, 7);
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

type EvidenceClock = {
  availableAt?: string | null;
  publishedAt?: string | null;
  retrievedAt?: string | null;
};

function visibleBy(row: EvidenceClock, cutoff?: string | null) {
  if (!cutoff) return true;
  const clocks = [row.availableAt, row.publishedAt, row.retrievedAt].filter(
    Boolean,
  );
  return clocks.length > 0 && clocks.every((clock) => knownBy(clock, cutoff));
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
              visibleBy(row, cutoff))),
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

export type CyclePoint = { date: string; value: number; basis: string };

export const cycleMonth = (day: string) =>
  Number(day.slice(0, 4)) * 12 + Number(day.slice(5, 7)) - 1;

export function cycleVix(data: OpportunityContext, cutoff?: string | null) {
  const series = data.series?.find((row) => row.code === "VIX");
  const points = new Map<string, number>();
  for (const row of series?.history ?? []) {
    if (
      row.value != null &&
      Number(row.value) > 0 &&
      validCycleSample(row) &&
      knownBy(row.date, cutoff) &&
      visibleBy(row, cutoff)
    ) {
      points.set(row.date.slice(0, 10), Number(row.value));
    }
  }
  if (
    series?.asOf &&
    series.value != null &&
    Number(series.value) > 0 &&
    validCycleSample(series) &&
    knownBy(series.asOf, cutoff) &&
    visibleBy(series, cutoff)
  ) {
    points.set(series.asOf.slice(0, 10), Number(series.value));
  }
  const sorted = [...points].sort(([left], [right]) =>
    left.localeCompare(right),
  );
  const last = sorted.at(-1);
  const end = cutoff?.slice(0, 10) ?? last?.[0];
  const start = end ? `${Number(end.slice(0, 4)) - 2}${end.slice(4)}` : "";
  return {
    points: sorted.filter(([day]) => day >= start),
    latest: last,
    sourceUri: series?.sourceUri,
  };
}

export function vixBand(value: number) {
  return value < 15 ? 0 : value < 25 ? 1 : value < 35 ? 2 : 3;
}

/** Evidence-based presentation bands, independent from stock volatility. */
export function liquidityBand(facts: CycleFact[], rateState?: string) {
  const read = (code: string) => facts.find((row) => row.code === code);
  const value = (code: string) => {
    const row = read(code);
    return row ? Number(row.value) : null;
  };
  const readings: number[] = [];
  const nominal = value("US_T10_NOMINAL");
  const real = value("US_T10_REAL");
  if (nominal !== null && real !== null) {
    readings.push(
      nominal > 4.5 || real > 2.5 ? 1 : nominal < 3.5 && real < 1.5 ? -1 : 0,
    );
  }
  const policy = value("US_FED_FUNDS");
  const treasury2y = value("US_T2_NOMINAL");
  if (rateState === "TIGHTER" || rateState === "EASIER")
    readings.push(rateState === "TIGHTER" ? 1 : -1);
  else if (
    policy !== null &&
    treasury2y !== null &&
    read("US_FED_FUNDS")?.asOf?.slice(0, 10) ===
      read("US_T2_NOMINAL")?.asOf?.slice(0, 10)
  ) {
    readings.push(
      treasury2y - policy >= 0.25 ? 1 : treasury2y - policy <= -0.25 ? -1 : 0,
    );
  }
  const m1 = read("M1_YOY");
  const m2 = read("M2_YOY");
  const m1Month = m1?.referencePeriod ?? m1?.asOf?.slice(0, 7);
  const m2Month = m2?.referencePeriod ?? m2?.asOf?.slice(0, 7);
  const gap =
    value("M1_M2_GAP") ??
    (m1 && m2 && m1Month && m1Month === m2Month
      ? Number(m1.value) - Number(m2.value)
      : null);
  if (gap !== null) readings.push(gap < -2 ? 1 : gap > 0 ? -1 : 0);
  const cn10 = value("CN_T10");
  const shibor = value("SHIBOR_7D") ?? value("DR007");
  if (cn10 !== null && shibor !== null) readings.push(shibor >= cn10 ? 1 : 0);
  if (readings.length < 2) return null;
  const score = readings.reduce((sum, item) => sum + item, 0);
  return score >= 2 ? 3 : score > 0 ? 2 : score <= -2 ? 0 : 1;
}

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
  const latest = series?.asOf ? series : monthly?.asOf ? monthly : undefined;
  if (
    latest?.asOf &&
    !history.some(
      (row) =>
        (row.referencePeriod ?? row.date).slice(0, 7) ===
        (latest.referencePeriod ?? latest.asOf!).slice(0, 7),
    )
  ) {
    history.push({
      ...latest,
      date: latest.asOf,
      timeBasis: series?.timeBasis ?? undefined,
      definitionKey: undefined,
      definitionVersion: undefined,
      metadata: series?.metadata
        ? {
            definitionKey: series.metadata.definitionKey ?? undefined,
            definitionVersion: series.metadata.definitionVersion ?? undefined,
          }
        : undefined,
    });
  }
  const usable = new Map<string, CyclePoint>();
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
      !visibleBy(row, cutoff)
    )
      continue;
    const month = (row.referencePeriod ?? row.date).slice(0, 7);
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
  return [...usable]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, point]) => ({ ...point, date: month }))
    .slice(-36);
}

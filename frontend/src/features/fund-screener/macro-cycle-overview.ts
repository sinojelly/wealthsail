import type { OpportunityContext } from "../../api/investment";
import type { MacroCycleInsight } from "../../api/macro-cycle";
import { researchSummary } from "./research-reading";

export type CycleView = MacroCycleInsight & { origin?: string };

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
  UNKNOWN: "阶段未单列",
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
            "阶段未单列")
          : "阶段未单列",
        driver: direction.industryCycle?.evidenceSummary || direction.thesis,
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
] as const;

export function cycleFacts(data: OpportunityContext) {
  const observations = [
    ...data.items,
    ...(data.series ?? []),
    ...(data.alphaPublic?.observations ?? []),
  ];
  return factDefinitions.flatMap(([code, label, unit]) => {
    const item = observations
      .filter(
        (row) =>
          row.code === code &&
          row.value != null &&
          String(row.value).trim() !== "" &&
          Number.isFinite(Number(row.value)) &&
          row.state !== "CONFLICT" &&
          row.state !== "UNAVAILABLE",
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

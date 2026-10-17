import { useState, type ReactNode } from "react";
import type { OpportunityContext } from "../../../api/investment";
import {
  macroUnit,
  metricValue,
  monthLabel,
  timeLabel,
} from "../macro-display";
import type { MacroItem } from "../macro-display";
import { SeriesCard } from "./OpportunityRadar";
import { EnergyObservations } from "./DailyResearchReport";
import "../macro-evidence.css";

const categories = [
  { id: "growth", label: "经济增长", codes: ["PMI"] },
  { id: "inflation", label: "通胀与成本", codes: ["CPI_YOY", "PPI_YOY"] },
  {
    id: "credit",
    label: "利率与信用",
    codes: [
      "M2_YOY",
      "CN_T10",
      "DR007",
      "M1_YOY",
      "TSF_STOCK_YOY",
      "CORP_MEDLONG_LOANS_YTD",
    ],
  },
  { id: "global", label: "全球风险与市场", codes: [] },
] as const;
const categoryFor = (code: string) =>
  categories.find((category) =>
    (category.codes as readonly string[]).includes(code),
  ) ?? categories[3];

export default function MacroEvidencePanel({
  data,
  renderMacro,
  marketId,
}: {
  data: OpportunityContext;
  renderMacro: (item: MacroItem) => ReactNode;
  marketId?: string;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const rows = [
    ...data.items.map((item) => ({
      code: item.code,
      label: item.label,
      value: item.value,
      unit: macroUnit(item),
      date: monthLabel(item.referencePeriod ?? item.asOf),
      state: ["STALE", "CONFLICT", "UNAVAILABLE"].includes(item.state)
        ? item.state
        : (item.valueVerification ?? item.state),
      detail: () => renderMacro(item),
    })),
    ...(data.series ?? []).map((item) => ({
      code: item.code,
      label: item.label,
      value: item.value,
      unit: item.unit,
      date: timeLabel(item.asOf),
      state: item.state,
      detail: () => <SeriesCard series={item} />,
    })),
  ];
  const preferred =
    marketId === "gold" || marketId === "us"
      ? ["US_T10_REAL", "US_T10_NOMINAL", "DXY", "USD_CNY", "PMI", "CPI_YOY"]
      : [
          "PMI",
          "CPI_YOY",
          "M2_YOY",
          "CN_T10",
          "M1_YOY",
          "TSF_STOCK_YOY",
          "US_T10_NOMINAL",
          "US_T10_REAL",
        ];
  const core = [...rows]
    .sort((a, b) => {
      const rank = (code: string) =>
        preferred.includes(code) ? preferred.indexOf(code) : 99;
      return rank(a.code) - rank(b.code);
    })
    .slice(0, 6);
  const remaining = rows.filter((item) => !core.includes(item));
  const active = rows.find((row) => row.code === selected);
  const energy = data.dailyResearch?.report?.energy ?? [];
  function compact(row: (typeof rows)[number]) {
    return (
      <article
        key={row.code}
        className="op-compact-metric"
        aria-label={row.label}
      >
        <strong>{row.label}</strong>
        <p>
          {metricValue(row.value, row.code === "PMI" ? 1 : 2)}{" "}
          {row.value == null || !Number.isFinite(row.value) ? "" : row.unit}
        </p>
        <small>
          {categoryFor(row.code).label} · {row.date} ·{" "}
          {row.state === "READY" || row.state === "VERIFIED"
            ? "已采集观察"
            : row.state === "STALE"
              ? "旧期证据"
              : row.state === "CONFLICT"
                ? "证据冲突"
                : "来源或状态待核验"}
        </small>
        <button
          type="button"
          aria-expanded={selected === row.code}
          onClick={() => setSelected(selected === row.code ? null : row.code)}
        >
          {selected === row.code ? "收起" : "查看"}
          {row.label}完整证据
        </button>
      </article>
    );
  }
  return (
    <div className="op-evidence-panel">
      <section aria-label="核心指标">
        <h3>核心指标</h3>
        <div className="op-compact-metrics">{core.map(compact)}</div>
        {!core.length && <p>尚无已采集的核心指标；未知数值不作为零。</p>}
      </section>
      <section className="op-evidence-groups" aria-label="其余证据分组">
        <h3>其余证据</h3>
        {categories
          .filter(
            (category) =>
              remaining.some(
                (row) => categoryFor(row.code).id === category.id,
              ) ||
              (category.id === "inflation" && energy.length > 0),
          )
          .map((category) => (
            <div key={category.id}>
              <button
                type="button"
                aria-expanded={group === category.id}
                onClick={() =>
                  setGroup(group === category.id ? null : category.id)
                }
              >
                {category.label}
              </button>
              {group === category.id && (
                <div className="op-compact-metrics">
                  {remaining
                    .filter((row) => categoryFor(row.code).id === category.id)
                    .map(compact)}
                  {category.id === "inflation" && (
                    <EnergyObservations rows={energy} />
                  )}
                </div>
              )}
            </div>
          ))}
      </section>
      {active && (
        <section
          key={active.code}
          className="op-evidence-detail"
          aria-label={`${active.label}证据详情`}
        >
          {active.detail()}
        </section>
      )}
    </div>
  );
}

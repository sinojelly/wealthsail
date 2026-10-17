import { useId, useState, type ReactNode } from "react";
import type {
  MarketEvidence,
  OpportunityContext,
} from "../../../api/investment";
import {
  changeLabel,
  macroUnit,
  metricValue,
  monthLabel,
  sourceHref,
  timeLabel,
  type MacroItem,
} from "../macro-display";
import { EvidenceCard, SeriesCard } from "./EvidenceCards";
import EnergyEvidence from "./EnergyEvidence";
import { energyUnit } from "../energy-display";
import "../macro-evidence.css";

const categories = [
  { id: "growth", label: "经济增长", codes: ["PMI"] },
  {
    id: "inflation",
    label: "通胀与成本",
    codes: ["CPI_YOY", "PPI_YOY", "DCOILBRENTEU", "GASDESW"],
  },
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
type IndicatorId =
  | `macro:${string}`
  | `series:${string}`
  | `energy:${string}`
  | `index:${string}:${"trend" | "pe"}`;
type EvidenceRow = {
  id: IndicatorId;
  code: string;
  label: string;
  value: number | null;
  unit: string | null;
  date: string;
  state: string;
  valueLabel?: string;
  change?: string;
  reference?: string;
  summary: string;
  detail: () => ReactNode;
};
type Props = {
  data: OpportunityContext;
  renderMacro?: (item: MacroItem) => ReactNode;
  marketId?: string;
  directionId?: string;
  selectedIndexId?: string;
  months?: number;
};
const finite = (value: number | null | undefined): value is number =>
  value != null && Number.isFinite(value);
const usable = (row: EvidenceRow) =>
  finite(row.value) &&
  ["READY", "VERIFIED", "STALE", "OBSERVED"].includes(row.state);
const statusLabels: Record<string, string> = {
  READY: "已采集观察",
  VERIFIED: "数值已核对",
  OBSERVED: "已采集观察",
  STALE: "旧期证据，需更新",
  CONFLICT: "证据冲突",
  UNAVAILABLE: "暂无可靠数值",
  NOT_COLLECTED: "尚未采集",
  PERMISSION_REQUIRED: "需要来源授权",
  UNVERIFIED: "数值来源待核对",
  PENDING: "数值来源待核对",
};
const statusLabel = (state: string) =>
  statusLabels[state] ?? "来源或状态待核验";
const percent = (value: number | null | undefined) =>
  finite(value) ? `${metricValue(value * 100)}%` : "暂不可计算";

function IndexEvidence({
  index,
  kind,
}: {
  index: MarketEvidence;
  kind: "trend" | "pe";
}) {
  const pe = index.valuation.pe;
  const href = sourceHref(
    kind === "pe" ? (pe?.sourceUri ?? index.sourceUri) : index.sourceUri,
  );
  return (
    <article className="op-evidence-card op-index-evidence">
      <strong>
        {index.label}
        {kind === "trend" ? "价格趋势" : "PE"}
      </strong>
      <div className="op-evidence-layout">
        <p>
          当前接口未提供可绘制的指数历史序列，以下保留已采集事实；完整指数研究见下方工作台。
        </p>
        <div className="op-evidence-facts">
          {kind === "trend" ? (
            <>
              <p className="op-series-value">
                相对MA200：{percent(index.trend.distanceMA200)}
              </p>
              <p>63期动量：{percent(index.trend.momentum63)}</p>
              <p>MA200：{metricValue(index.trend.ma200)}</p>
              {index.trendWindow && (
                <p>
                  实际趋势窗口：{timeLabel(index.trendWindow.start)} 至{" "}
                  {timeLabel(index.trendWindow.end)} ·{" "}
                  {index.trendWindow.sampleCount} 个实际样本
                </p>
              )}
              {index.historyQuality !== "READY" && (
                <p>
                  当前窗口不代表完整历史已核验；完整历史仍有缺期、冲突或待核查证据。
                </p>
              )}
              <p>
                指数价格趋势可作为双轨研究依据，基金价格仍使用对应标的口径。
              </p>
            </>
          ) : (
            <>
              <p className="op-series-value">{metricValue(pe?.latest)} 倍</p>
              <p>PE倍数为独立事实；低估分支使用已核验历史分位。</p>
              <p>
                已采集 {pe?.sampleCount ?? "未知数量"} 个估值样本；
                {pe?.percentile == null
                  ? "历史分位暂不可计算。"
                  : `已有历史分位：${metricValue(pe.percentile)}%。`}
              </p>
              {index.valuation.summary && <p>{index.valuation.summary}</p>}
            </>
          )}
          <p>观察期：{timeLabel(index.asOf)}</p>
        </div>
      </div>
      <details className="op-evidence-metadata">
        <summary>查看{index.label}数据依据</summary>
        {!!index.missingEvidence.length && (
          <p>待核查：{index.missingEvidence.join("、")}</p>
        )}
        <p>历史时点与回测验证保留各自原始状态。</p>
        {href && (
          <a href={href} target="_blank" rel="noreferrer">
            查看{index.label}证据来源
          </a>
        )}
      </details>
    </article>
  );
}

function evidenceRows({
  data,
  renderMacro,
  selectedIndexId,
  directionId,
}: Props): EvidenceRow[] {
  const rows: EvidenceRow[] = [
    ...data.items.map((item): EvidenceRow => ({
      id: `macro:${item.code}`,
      code: item.code,
      label: item.label,
      value: item.value,
      unit: macroUnit(item),
      date: monthLabel(item.referencePeriod ?? item.asOf),
      state: ["CONFLICT", "UNAVAILABLE"].includes(item.state)
        ? item.state
        : item.valueVerification && item.valueVerification !== "VERIFIED"
          ? item.valueVerification
          : item.state === "STALE"
            ? "STALE"
            : (item.valueVerification ?? item.state),
      change: `连续月变化：${changeLabel(item.periodChange, item)}`,
      reference:
        item.benchmark && finite(item.benchmark.value)
          ? `${item.benchmark.label}：${metricValue(item.benchmark.value)} ${macroUnit(item)}`
          : undefined,
      summary: item.interpretation?.summary ?? "已采集事实，经济含义仍需核查。",
      detail: () =>
        renderMacro ? renderMacro(item) : <EvidenceCard item={item} />,
    })),
    ...(data.series ?? []).map((item): EvidenceRow => {
      const change =
        item.frequency === "MONTHLY"
          ? item.changes.periodChange
          : item.changes.change5Samples;
      const unit =
        item.changes.changeUnit === "bp"
          ? "基点"
          : item.changes.changeUnit === "pp"
            ? "个百分点"
            : null;
      return {
        id: `series:${item.code}`,
        code: item.code,
        label: item.label,
        value: item.value,
        unit: item.unit,
        date: timeLabel(item.asOf),
        state: item.state,
        change:
          item.frequency === "YTD"
            ? "年内累计值，不跨年比较。"
            : `${item.frequency === "MONTHLY" ? "连续月变化" : "近5个可比较样本"}：${finite(change) && unit ? `${change > 0 ? "+" : ""}${metricValue(change)} ${unit}` : "暂不可计算"}`,
        reference: finite(item.changes.median)
          ? `历史样本中位数：${metricValue(item.changes.median)} ${item.unit ?? ""}`
          : undefined,
        summary: "实际市场观察，需结合当前资产与其他证据独立判断。",
        detail: () => <SeriesCard series={item} />,
      };
    }),
    ...(data.dailyResearch?.report?.energy ?? []).map((item): EvidenceRow => ({
      id: `energy:${item.code}`,
      code: item.code,
      label: item.label,
      value: item.value,
      unit: energyUnit(item.unit),
      date: timeLabel(item.asOf),
      state: item.state,
      change: `相邻变化：${finite(item.change) ? `${item.change > 0 ? "+" : ""}${item.change.toFixed(3)} ${energyUnit(item.unit)}` : "暂不可计算"}`,
      summary: "能源价格观察，不认证新闻因果或企业利润变化。",
      detail: () => <EnergyEvidence row={item} />,
    })),
  ];
  const index =
    directionId === "a-share:broad" && selectedIndexId
      ? data.marketEvidence?.find((row) => row.indexId === selectedIndexId)
      : undefined;
  if (index) {
    rows.push({
      id: `index:${index.indexId}:trend`,
      code: "INDEX_TREND",
      label: `${index.label}价格趋势`,
      value: finite(index.trend.distanceMA200)
        ? index.trend.distanceMA200 * 100
        : null,
      unit: "%",
      date: timeLabel(index.asOf),
      state: index.trend.state,
      valueLabel: `相对MA200：${percent(index.trend.distanceMA200)}`,
      change: `63期动量：${percent(index.trend.momentum63)}`,
      summary: "指数趋势观察，双轨区间见下方工作台。",
      detail: () => <IndexEvidence index={index} kind="trend" />,
    });
    const pe = index.valuation.pe;
    rows.push({
      id: `index:${index.indexId}:pe`,
      code: "INDEX_PE",
      label: `${index.label}PE`,
      value: pe?.latest ?? null,
      unit: "倍",
      date: timeLabel(index.asOf),
      // Insufficient valuation history does not erase an independent PE observation or certify qualification.
      state:
        pe &&
        ["READY", "INSUFFICIENT"].includes(pe.state) &&
        ["READY", "PARTIAL"].includes(index.valuation.state)
          ? "OBSERVED"
          : (pe?.state ?? "UNAVAILABLE"),
      summary: "PE倍数为独立事实；低估分支使用已核验历史分位。",
      detail: () => <IndexEvidence index={index} kind="pe" />,
    });
  }
  return [...new Map(rows.map((row) => [row.id, row])).values()];
}

export default function MacroEvidencePanel(props: Props) {
  const rows = evidenceRows(props);
  // Reordering preserves identity. A disappearing identity or new research scope discards its selection.
  const scope = JSON.stringify([
    props.directionId,
    props.marketId,
    props.selectedIndexId,
    props.months ?? props.data.months,
    rows.map((row) => `${row.id}:${usable(row)}`).sort(),
  ]);
  return (
    <EvidenceContent
      key={scope}
      rows={rows}
      marketId={props.marketId}
      directionId={props.directionId}
    />
  );
}

function EvidenceContent({
  rows,
  marketId,
  directionId,
}: {
  rows: EvidenceRow[];
  marketId?: string;
  directionId?: string;
}) {
  const [selected, setSelected] = useState<IndicatorId | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const panelId = useId();
  const preferred =
    directionId === "a-share:cyclical"
      ? ["PPI_YOY", "DCOILBRENTEU", "GASDESW", "PMI", "CPI_YOY", "M2_YOY"]
      : marketId === "gold" || marketId === "us"
        ? ["US_T10_REAL", "US_T10_NOMINAL", "DXY", "USD_CNY", "PMI", "CPI_YOY"]
        : [
            "INDEX_TREND",
            "INDEX_PE",
            "PMI",
            "CPI_YOY",
            "M2_YOY",
            "CN_T10",
            "M1_YOY",
            "TSF_STOCK_YOY",
            "US_T10_NOMINAL",
            "US_T10_REAL",
          ];
  const available = rows.filter(usable),
    pending = rows.filter((row) => !usable(row));
  const rank = (code: string) =>
    preferred.includes(code) ? preferred.indexOf(code) : 99;
  const core = [...available]
    .sort((a, b) => rank(a.code) - rank(b.code))
    .slice(0, 6);
  const remaining = available.filter((row) => !core.includes(row));
  const active = rows.find((row) => row.id === selected);
  const detailId = (row: EvidenceRow) => `${panelId}-${row.id}-detail`;
  function toggleGroup(id: string) {
    setSelected(null);
    setGroup(group === id ? null : id);
  }
  function evidenceButton(row: EvidenceRow) {
    return (
      <button
        type="button"
        aria-expanded={selected === row.id}
        aria-controls={detailId(row)}
        onClick={() => setSelected(selected === row.id ? null : row.id)}
      >
        {selected === row.id ? "收起" : "查看"}
        {row.label}完整证据
      </button>
    );
  }
  function compact(row: EvidenceRow) {
    return (
      <article
        key={row.id}
        className="op-compact-metric"
        aria-label={row.label}
      >
        <strong>{row.label}</strong>
        <p>
          {row.valueLabel ??
            `${row.id.startsWith("energy:") && finite(row.value) ? row.value.toFixed(3) : metricValue(row.value, row.code === "PMI" ? 1 : 2)} ${row.unit ?? ""}`.trim()}
        </p>
        <small>
          {categoryFor(row.code).label} · {row.date} · {statusLabel(row.state)}
        </small>
        {row.change && <small>{row.change}</small>}
        {row.reference && <small>{row.reference}</small>}
        <span className="op-compact-explanation">{row.summary}</span>
        {evidenceButton(row)}
      </article>
    );
  }
  function detail(owner: EvidenceRow[]) {
    return active && owner.includes(active) ? (
      <section
        key={active.id}
        id={detailId(active)}
        className="op-evidence-detail"
        aria-label={`${active.label}证据详情`}
      >
        {active.detail()}
      </section>
    ) : null;
  }
  return (
    <div className="op-evidence-panel">
      <section aria-label="核心指标">
        <h3>核心指标</h3>
        <div className="op-compact-metrics">{core.map(compact)}</div>
        {!core.length && <p>尚无已采集的核心指标；未知数值不作为零。</p>}
        {detail(core)}
      </section>
      {!!remaining.length && (
        <section className="op-evidence-groups" aria-label="其余证据分组">
          <h3>其余证据</h3>
          {categories
            .filter((category) =>
              remaining.some((row) => categoryFor(row.code).id === category.id),
            )
            .map((category) => {
              const members = remaining.filter(
                (row) => categoryFor(row.code).id === category.id,
              );
              return (
                <section
                  key={category.id}
                  className="op-evidence-group"
                  aria-label={`${category.label}证据`}
                >
                  <button
                    type="button"
                    aria-expanded={group === category.id}
                    aria-controls={`${panelId}-${category.id}`}
                    onClick={() => toggleGroup(category.id)}
                  >
                    {category.label}
                  </button>
                  {group === category.id && (
                    <div id={`${panelId}-${category.id}`}>
                      <div className="op-compact-metrics">
                        {members.map(compact)}
                      </div>
                      {detail(members)}
                    </div>
                  )}
                </section>
              );
            })}
        </section>
      )}
      {!!pending.length && (
        <section className="op-evidence-pending" aria-label="待补充证据">
          <button
            type="button"
            aria-expanded={group === "pending"}
            aria-controls={`${panelId}-pending`}
            onClick={() => toggleGroup("pending")}
          >
            待补充证据
          </button>
          <small>
            {pending.map((row) => row.label).join("、")}仍待补充或核对。
          </small>
          {group === "pending" && (
            <section id={`${panelId}-pending`} aria-label="待补充证据清单">
              <ul>
                {pending.map((row) => (
                  <li key={row.id}>
                    <span>
                      {row.label} · {!finite(row.value) && "暂无有效数值 · "}
                      {statusLabel(row.state)} · {row.date}
                    </span>
                    {evidenceButton(row)}
                  </li>
                ))}
              </ul>
              {detail(pending)}
            </section>
          )}
        </section>
      )}
    </div>
  );
}

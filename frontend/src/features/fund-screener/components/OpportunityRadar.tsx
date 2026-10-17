import { useState } from "react";
import type {
  Direction,
  OpportunityContext,
  OpportunitySeries,
  RadarChange,
} from "../../../api/investment";
import {
  metricValue,
  monthLabel,
  sourceHref,
  timeLabel,
} from "../macro-display";
import { useEvidenceChart } from "./useEvidenceChart";

const states: Record<string, string> = {
  READY: "事实可用",
  PARTIAL: "部分覆盖",
  UNKNOWN: "待核查",
  NOT_COLLECTED: "尚未采集",
  UNAVAILABLE: "证据不可用",
  INSUFFICIENT: "证据不足",
  CONFLICT: "证据冲突",
  STALE: "旧期证据",
  PERMISSION_REQUIRED: "需要来源授权",
  PASS: "条件满足",
  BLOCKED: "条件未满足",
  WAIT: "等待证据",
  ENTRY_CANDIDATE: "建仓研究候选",
  HOLD_REVIEW: "持仓观察",
  POSITION_REVIEW: "持仓风险复核",
  NOT_HELD: "未持有",
  UNVERIFIED: "待核验",
  CONFIRMED: "已确认",
  OBSERVED: "事实观察",
  OBSERVATION: "事实观察",
  SUPPORT: "支持线索",
  RISK: "风险复核",
  GAP: "证据缺口",
  HELD: "已持有",
  BREACH: "已确认预算超限",
  CHECKED: "已完成预算核查",
  PRIORITY_RESEARCH: "值得优先研究",
  WAIT_CONDITIONS: "等待条件",
  RISK_OBSERVATION: "风险观察",
};
const stateText = (state?: string) => states[state ?? ""] ?? "待核查";
function evidenceLabel(id: string, data: OpportunityContext) {
  const known: Record<string, string> = {
    INDEX_TREND: "当前指数趋势",
    INDEX_VALUATION: "当前指数估值",
    INDEX_EARNINGS: "企业盈利证据",
    portfolio: "个人组合证据",
    CN_T10: "中国10年期国债收益率",
    DR007: "银行间存款类机构7天回购利率",
    US_T10_NOMINAL: "美国10年期名义国债收益率",
    US_T10_REAL: "美国10年期实际利率",
    M1_YOY: "M1同比",
    TSF_STOCK_YOY: "社融存量同比",
  };
  return (
    data.series?.find((s) => s.code === id)?.label ??
    data.items.find((i) => i.code === id)?.label ??
    data.marketEvidence?.find((i) => i.indexId === id)?.label ??
    data.events?.find((e) => e.id === id)?.title ??
    known[id] ??
    id
  );
}
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const number = (value: unknown) => (finite(value) ? value.toFixed(2) : "—");
const percent = (value: unknown) =>
  finite(value) ? `${(value * 100).toFixed(2)}%` : "—";
const dateLabel = (raw: string | null | undefined) => {
  if (!raw) return "待确认";
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
  return match
    ? `${match[1]}年${Number(match[2])}月${Number(match[3])}日`
    : timeLabel(raw);
};
function calendarDate(raw: unknown): raw is string {
  if (typeof raw !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(raw)) return false;
  const value = Date.parse(`${raw}T00:00:00Z`);
  return (
    Number.isFinite(value) && new Date(value).toISOString().slice(0, 10) === raw
  );
}
function accumulationRange(series: OpportunitySeries) {
  const start = series.metadata?.accumulationStart;
  const end = series.metadata?.accumulationEnd;
  const reference = series.asOf;
  if (
    !calendarDate(start) ||
    !calendarDate(end) ||
    !calendarDate(reference) ||
    start > end ||
    start !== `${reference.slice(0, 4)}-01-01` ||
    end.slice(0, 7) !== reference.slice(0, 7)
  )
    return "待核查";
  return `${dateLabel(start)}—${dateLabel(end)}`;
}
const kindText: Record<RadarChange["kind"], string> = {
  RISK: "风险复核",
  SUPPORT: "支持线索",
  GAP: "证据缺口",
  OBSERVATION: "事实观察",
};

export function RadarOverview({
  data,
  selectedDirection,
}: {
  data: OpportunityContext;
  selectedDirection?: Direction;
}) {
  const radar = data.radar;
  if (!radar) return null;
  const headline = radar.headline;
  return (
    <section className="op-radar-overview" aria-label="投资机会四问">
      {selectedDirection && (
        <small aria-label="雷达研究范围">
          当前研究：{selectedDirection.marketLabel} · {selectedDirection.label}
        </small>
      )}
      <div className="op-radar-headline">
        <span className="op-badge">
          {headline.priority === "CRITICAL" && headline.kind !== "GAP"
            ? "值得复核"
            : headline.priority === "ATTENTION"
              ? "值得关注"
              : "研究观察"}{" "}
          · {kindText[headline.kind]}
        </span>
        <p>{headline.summary}</p>
        <p className={headline.kind === "GAP" ? "op-tone-gray" : undefined}>
          {headline.impact}
        </p>
        <small>
          {headline.timeScale} · 研究时点：{timeLabel(radar.asOf)}
        </small>
      </div>
      <dl className="op-radar-questions">
        {radar.questions.map((q) => (
          <div key={q.id}>
            <dt>
              {q.label}
              <span className="op-badge">{stateText(q.state)}</span>
            </dt>
            <dd>{q.summary}</dd>
          </div>
        ))}
      </dl>
      <div className="op-radar-secondary">
        {radar.changes
          .filter((c) => c.id !== headline.id)
          .slice(0, 2)
          .map((change) => (
            <p key={change.id}>
              <strong>{change.summary}</strong> · {change.timeScale}
            </p>
          ))}
      </div>
    </section>
  );
}

function SeriesHistory({ series }: { series: OpportunitySeries }) {
  const [selected, setSelected] = useState<number | null>(null);
  const [plotRef, chart] = useEvidenceChart();
  const history = series.history
    .map((p, index) => ({ ...p, index }))
    .filter(
      (p) =>
        finite(p.value) &&
        Number.isFinite(Date.parse(p.date)) &&
        ["READY", "STALE", "OBSERVED"].includes(p.state),
    );
  if (history.length < 2) return <p>可用历史样本不足，暂不绘制走势。</p>;
  if (new Set(history.map((point) => point.unit ?? series.unit)).size > 1)
    return <p>历史单位发生变化，暂不绘制可比较走势。</p>;
  const values = history.map((p) => p.value as number),
    low = Math.min(...values),
    high = Math.max(...values),
    padding = Math.max((high - low) * 0.1, 0.1);
  const first = Date.parse(history[0].date),
    last = Date.parse(history.at(-1)!.date);
  const y = (v: number) =>
    chart.bottom -
    ((v - low + padding) / (high - low + padding * 2)) *
      (chart.bottom - chart.top);
  const points = history.map((p) => ({
    ...p,
    x:
      chart.left +
      ((Date.parse(p.date) - first) / (last - first || 1)) *
        (chart.right - chart.left),
    y: y(p.value as number),
    basis:
      (p.definitionKey ?? p.metadata?.definitionKey)
        ? `${p.definitionKey ?? p.metadata?.definitionKey}:${p.definitionVersion ?? p.metadata?.definitionVersion ?? ""}`
        : (p.sourceUri ?? series.sourceName ?? series.sourceUri),
    unit: p.unit ?? series.unit,
  }));
  const active = points.find((p) => p.index === selected);
  function consecutive(a: (typeof points)[number], b: (typeof points)[number]) {
    if (
      b.index - a.index !== 1 ||
      !a.basis ||
      a.basis !== b.basis ||
      a.unit == null ||
      a.unit !== b.unit
    )
      return false;
    if (series.frequency === "MONTHLY")
      return (
        Number(b.date.slice(0, 4)) * 12 +
          Number(b.date.slice(5, 7)) -
          Number(a.date.slice(0, 4)) * 12 -
          Number(a.date.slice(5, 7)) ===
        1
      );
    return (
      series.frequency !== "YTD" || a.date.slice(0, 4) === b.date.slice(0, 4)
    );
  }
  return (
    <figure
      ref={plotRef}
      className="op-macro-chart op-series-chart op-evidence-plot"
    >
      <svg
        role="img"
        aria-label={`${series.label} 历史观察`}
        viewBox={`0 0 ${chart.width} ${chart.height}`}
        height={chart.height}
        width="100%"
      >
        <title>{series.label} 实际观察样本，冲突及口径改变不连接</title>
        <text x={4} y={14}>
          {series.unit ?? "单位待确认"}
        </text>
        {[low - padding, (low + high) / 2, high + padding].map((v) => (
          <g key={v}>
            <text x={chart.left - 6} y={y(v) + 4} textAnchor="end">
              {v.toFixed(2)}
            </text>
            <path
              d={`M${chart.left} ${y(v)} H${chart.right}`}
              stroke="#e2e8f0"
              fill="none"
            />
          </g>
        ))}
        {points.map((p, i) => (
          <g key={`${p.date}:${p.index}`}>
            {i > 0 && consecutive(points[i - 1], p) && (
              <line
                x1={points[i - 1].x}
                y1={points[i - 1].y}
                x2={p.x}
                y2={p.y}
                stroke="#0d9488"
                strokeWidth={2}
              />
            )}
            <circle
              role="button"
              tabIndex={0}
              aria-label={`查看${series.label} ${dateLabel(p.date)}`}
              cx={p.x}
              cy={p.y}
              r={4}
              fill="#0d9488"
              onFocus={() => setSelected(p.index)}
              onMouseEnter={() => setSelected(p.index)}
              onClick={() => setSelected(p.index)}
              onKeyDown={(event) => {
                if (["Enter", " "].includes(event.key)) {
                  event.preventDefault();
                  setSelected(p.index);
                }
              }}
            />
          </g>
        ))}
        <text x={chart.left} y={chart.height - 14}>
          {chart.width < 420
            ? dateLabel(history[0].date).replace(/^\d+年/, "")
            : dateLabel(history[0].date)}
        </text>
        <text x={chart.right} y={chart.height - 14} textAnchor="end">
          {chart.width < 420
            ? dateLabel(history.at(-1)!.date).replace(/^\d+年/, "")
            : dateLabel(history.at(-1)!.date)}
        </text>
      </svg>
      <figcaption>
        {series.frequency === "DAILY"
          ? "日度实际观察样本"
          : series.frequency === "YTD"
            ? "年内累计观察，跨年不连接"
            : "月度实际观察样本"}{" "}
        · {history.length} 个可绘制样本 · {dateLabel(history[0].date)} 至{" "}
        {dateLabel(history.at(-1)!.date)}
      </figcaption>
      <p role="status" aria-label={`${series.label} 历史明细`}>
        {active
          ? `${dateLabel(active.date)}：${active.value} ${active.unit ?? "单位待确认"} · 来源：${active.sourceName ?? series.sourceName ?? "待确认"} · 可得：${timeLabel(active.availableAt ?? active.retrievedAt)}`
          : "悬停或键盘聚焦查看实际观察值与来源。"}
      </p>
    </figure>
  );
}

export function SeriesCard({ series }: { series: OpportunitySeries }) {
  const href = sourceHref(series.sourceUri),
    changeUnit =
      series.changes.changeUnit === "bp"
        ? "基点"
        : series.changes.changeUnit === "pp"
          ? "个百分点"
          : null;
  const change = (value: number | null | undefined) =>
    finite(value) && changeUnit
      ? `${value > 0 ? "+" : ""}${value.toFixed(2)} ${changeUnit}`
      : "暂不可计算";
  return (
    <article className="op-series-card op-evidence-card">
      <h3>{series.label}</h3>
      <span className="op-badge">{stateText(series.state)}</span>
      <div className="op-evidence-layout">
        {series.state === "PERMISSION_REQUIRED" ? (
          <p>暂无获得授权的历史序列。</p>
        ) : (
          <SeriesHistory series={series} />
        )}
        <div className="op-evidence-facts">
          <p className="op-series-value">
            {finite(series.value)
              ? `${metricValue(series.value)} ${series.unit ?? "单位待确认"}`
              : "—"}
          </p>
          <p>
            {series.frequency === "YTD"
              ? `累计区间：${accumulationRange(series)}`
              : `观察期：${series.frequency === "MONTHLY" ? monthLabel(series.asOf) : dateLabel(series.asOf)}`}{" "}
            ·{" "}
            {series.frequency === "DAILY"
              ? "日度"
              : series.frequency === "YTD"
                ? "年内累计"
                : "月度"}
          </p>
          {series.state === "PERMISSION_REQUIRED" ? (
            <p>需要来源授权，当前未自动采集。</p>
          ) : (
            <>
              <p>
                前一同口径有效值：{number(series.changes.previousValue)}{" "}
                {series.unit ?? ""}
              </p>
              {series.frequency === "YTD" ? (
                <p>年内累计值，未经核实不计算单月差，也不跨年比较。</p>
              ) : series.frequency === "MONTHLY" ? (
                <>
                  <p>连续月变化：{change(series.changes.periodChange)}</p>
                  <p>近3月变化：{change(series.changes.change3Months)}</p>
                  <p>近6月变化：{change(series.changes.change6Months)}</p>
                </>
              ) : (
                <>
                  <p>
                    近5个可比较观察样本：{change(series.changes.change5Samples)}
                  </p>
                  <p>
                    近20个可比较观察样本：
                    {change(series.changes.change20Samples)}
                  </p>
                </>
              )}
              <p>
                历史中位数：{number(series.changes.median)} · 分位：
                {finite(series.changes.percentile)
                  ? `${series.changes.percentile.toFixed(2)}%`
                  : "暂不可计算"}
              </p>
            </>
          )}
        </div>
      </div>
      <details className="op-evidence-metadata">
        <summary>查看{series.label}数据依据</summary>
        {series.state !== "PERMISSION_REQUIRED" && (
          <>
            <p>
              统计基于 {series.changes.sampleCount} 个实际
              {series.frequency === "DAILY" ? "日" : "月"}样本
            </p>
            {series.publishedAt ? (
              <p>实际公布：{timeLabel(series.publishedAt)}</p>
            ) : (
              <p>实际公布时刻未确认。</p>
            )}
            <p>
              {series.timeBasis === "RETRIEVAL_ONLY"
                ? "获取时点可得"
                : "证据可得时点"}
              ：{timeLabel(series.availableAt)}；不将观察日午夜视作已公开。
            </p>
          </>
        )}
        <p>
          来源：{series.sourceName ?? "待确认"} · 获取：
          {timeLabel(series.retrievedAt)}
        </p>
        <p>方法：{series.methodVersion ?? "尚未提供"}</p>
        {href && series.state !== "PERMISSION_REQUIRED" && (
          <a href={href} target="_blank" rel="noreferrer">
            查看{series.label}来源
          </a>
        )}
      </details>
      {series.state === "PERMISSION_REQUIRED" && href && (
        <a href={href} target="_blank" rel="noreferrer">
          查看{series.label}来源
        </a>
      )}
    </article>
  );
}

export function RadarDetails({
  data,
  monitorEnabled = false,
  monitorPending = false,
  monitorError,
  monitorLoading = false,
  onMonitor,
  onNoticeAck,
  ackPending,
  ackError,
  section,
  hideTracking = false,
}: {
  data: OpportunityContext;
  monitorEnabled?: boolean;
  monitorPending?: boolean;
  monitorLoading?: boolean;
  monitorError?: string;
  onMonitor?: (enabled: boolean) => void;
  onNoticeAck?: (id: string) => void;
  ackPending?: string | null;
  ackError?: string;
  section?: "series" | "market" | "strategy" | "monitor";
  hideTracking?: boolean;
}) {
  const { radar, decision, portfolio, directionModel } = data;
  const [expandedReasons, setExpandedReasons] = useState<string[]>([]);
  const canonicalItems = data.researchBoard?.items ?? [];
  const canonicalTexts = new Set(
    canonicalItems.flatMap((item) =>
      [item.impact, item.blocker?.reason]
        .filter((text): text is string => !!text?.trim())
        .flatMap((text) => [
          text.trim(),
          ...text.split(/[；;\n]/).map((part) => part.trim()),
        ]),
    ),
  );
  const canonicalGateCodes: Record<string, string> = {
    product: "PRODUCT_QUALITY",
    tradability: "TRADABILITY",
    validation: "PIT_VALIDATION",
    portfolio: "PERSONAL_PORTFOLIO",
  };
  const covered = (text: string) => canonicalTexts.has(text.trim());
  const gateCovered = (gate: NonNullable<typeof decision>["gates"][number]) =>
    covered(gate.reason) ||
    (gate.state === "UNKNOWN" &&
      !!canonicalGateCodes[gate.id] &&
      canonicalItems.some(
        (item) => item.blocker?.code === canonicalGateCodes[gate.id],
      ));
  const remainingGateReasons = (decision?.gates ?? [])
    .filter((gate) => !gateCovered(gate))
    .map((gate) => gate.reason);
  const personalCovered = (text: string) =>
    covered(text) ||
    remainingGateReasons.some((reason) =>
      [
        reason.trim(),
        ...reason.split(/[；;\n]/).map((part) => part.trim()),
      ].includes(text.trim()),
    );
  const personalSummary =
    portfolio?.summary && !personalCovered(portfolio.summary)
      ? portfolio.summary
      : null;
  const personalMissing = [...new Set(portfolio?.missingEvidence ?? [])].filter(
    (reason) => !personalCovered(reason),
  );
  const toggleReason = (id: string) =>
    setExpandedReasons((previous) =>
      previous.includes(id)
        ? previous.filter((key) => key !== id)
        : [...previous, id],
    );
  const show = (name: NonNullable<typeof section>) =>
    !section || section === name;
  return (
    <div className="op-radar-details">
      {!section && !!radar?.changes.length && (
        <section aria-label="重要条件变化">
          <h3>按重要性排列的研究变化</h3>
          <ol className="op-radar-change-list">
            {radar.changes.map((c) => (
              <li key={c.id}>
                <strong>{c.summary}</strong>
                <span className="op-badge">
                  {c.priority === "CRITICAL"
                    ? "优先复核"
                    : c.priority === "ATTENTION"
                      ? "值得关注"
                      : "普通更新"}{" "}
                  · {kindText[c.kind]}
                </span>
                <p>{c.impact}</p>
                <small>
                  {c.timeScale} · 依据：
                  {c.evidenceIds
                    .map((id) => evidenceLabel(id, data))
                    .join("、") || "当前证据覆盖与缺口"}
                </small>
              </li>
            ))}
          </ol>
          <small>
            研究优先级用于复核条件，不代表交易收益或买卖排序。方法：
            {radar.methodVersion}
          </small>
        </section>
      )}
      {!section && !!radar?.sections.length && (
        <section className="op-radar-sections" aria-label="机会与风险专题">
          {radar.sections.map((s) => (
            <article key={s.id}>
              <h3>{s.label}</h3>
              <span className="op-badge">{stateText(s.state)}</span>
              <p>{s.summary}</p>
              {s.criticalImpact && <p>{s.criticalImpact}</p>}
              {!!s.missingEvidence.length && (
                <p>证据缺口：{s.missingEvidence.join("、")}</p>
              )}
            </article>
          ))}
        </section>
      )}
      {show("series") && !!data.series?.length && (
        <section aria-label="关键变量证据">
          <h3>
            {data.researchBoard ? "相关变量的实际证据" : "利率与信用的实际证据"}
          </h3>
          <div className="op-series-grid">
            {data.series.map((s) => (
              <SeriesCard key={s.code} series={s} />
            ))}
          </div>
        </section>
      )}
      {show("market") && !!data.marketEvidence?.length && (
        <section aria-label="当前指数证据">
          <h3>当前方向的指数与市场</h3>
          <div className="op-radar-market-grid">
            {data.marketEvidence.map((index) => (
              <article key={index.indexId}>
                <h3>{index.label}</h3>
                <small>观察期：{dateLabel(index.asOf)}</small>
                <p>
                  估值：{stateText(index.valuation.state)}
                  {index.valuation.summary
                    ? ` · ${index.valuation.summary}`
                    : ""}
                </p>
                {index.valuation.pe && (
                  <p>
                    PE：{number(index.valuation.pe.latest)} · 历史分位：
                    {number(index.valuation.pe.percentile)}%
                  </p>
                )}
                {index.valuation.pb && (
                  <p>
                    PB：{number(index.valuation.pb.latest)} · 历史分位：
                    {number(index.valuation.pb.percentile)}%
                  </p>
                )}
                <p>
                  趋势：{stateText(index.trend.state)} · MA200：
                  {number(index.trend.ma200)}
                </p>
                <p>
                  63期动量：{percent(index.trend.momentum63)} · 相对MA200：
                  {percent(index.trend.distanceMA200)}
                </p>
                {index.trendWindow && (
                  <p>
                    实际趋势窗口：{dateLabel(index.trendWindow.start)} 至{" "}
                    {dateLabel(index.trendWindow.end)} ·{" "}
                    {index.trendWindow.sampleCount} 个实际样本
                  </p>
                )}
                {index.historyQuality && index.historyQuality !== "READY" && (
                  <p>
                    完整历史质量：{stateText(index.historyQuality)}；
                    {index.historyQuality === "CONFLICT"
                      ? "完整历史仍有缺期或冲突；"
                      : "完整历史尚未就绪，仍需核查缺期或冲突；"}
                    当前趋势窗口不认证完整历史或历史时点（PIT）验证。
                  </p>
                )}
                <p>
                  风险：{stateText(index.risk.state)} · 20期波动：
                  {percent(index.risk.volatility20)}
                </p>
                <p>最大回撤：{percent(index.risk.maxDrawdown)}</p>
                <p>盈利：{stateText(index.earnings.state)}</p>
                {index.earnings.summary && <p>{index.earnings.summary}</p>}
                {!data.researchBoard && !!index.missingEvidence.length && (
                  <p>证据缺口：{index.missingEvidence.join("、")}</p>
                )}
                {sourceHref(index.sourceUri) && (
                  <a
                    href={sourceHref(index.sourceUri)}
                    target="_blank"
                    rel="noreferrer"
                  >
                    查看{index.label}证据来源
                  </a>
                )}
              </article>
            ))}
          </div>
        </section>
      )}
      {!section && directionModel && (
        <section aria-label="当前方向证据模型">
          <h3>方向支持与逆风</h3>
          <span className="op-badge">{stateText(directionModel.state)}</span>
          {directionModel.supports.map((e, i) => (
            <p key={`support-${i}`}>
              <strong>支持线索：{e.label}</strong> · {e.summary} ·{" "}
              {stateText(e.state)}
            </p>
          ))}
          {directionModel.counterFactors.map((e, i) => (
            <p key={`counter-${i}`}>
              <strong>反方条件：{e.label}</strong> · {e.summary} ·{" "}
              {stateText(e.state)}
            </p>
          ))}
          <p>待验证：{directionModel.watchFor.join("、") || "尚需明确条件"}</p>
          <p>
            必要证据：
            {directionModel.requiredEvidence
              .map((id) => evidenceLabel(id, data))
              .join("、") || "待确认"}
          </p>
          <p>
            证据缺口：
            {directionModel.missingEvidence.join("、") || "按上述条件独立核验"}
          </p>
          <small>方法：{directionModel.methodVersion}</small>
        </section>
      )}
      {show("strategy") && (decision || portfolio) && (
        <section aria-label="策略与个人持仓">
          <h3>策略与持仓</h3>
          <p>按每日盘后研究与持仓变更分别复核。</p>
          {decision && (
            <>
              <span className="op-badge">{stateText(decision.state)}</span>
              {!data.researchBoard && <p>{decision.summary}</p>}
              <p>
                市场条件：{stateText(decision.marketState)} · 个人条件：
                {stateText(decision.personalState)}
              </p>
              {decision.assessment && (
                <p>
                  持仓识别：{stateText(decision.assessment.holdingState)} ·
                  风险评估：
                  {decision.assessment.riskState === "UNKNOWN"
                    ? "尚未评估"
                    : stateText(decision.assessment.riskState)}{" "}
                  · 市场研究：
                  {stateText(decision.assessment.marketResearchState)}
                </p>
              )}
              {(
                [
                  ["PASS", "已满足的条件"],
                  ["BLOCKED", "确实未满足的条件"],
                  ["UNKNOWN", "无法核验的条件"],
                ] as const
              ).map(([state, label]) => (
                <section key={state} aria-label={label}>
                  <h4>{label}</h4>
                  <dl className="op-radar-gates">
                    {decision.gates
                      .filter((g) => g.state === state)
                      .map((g) => (
                        <div key={g.id}>
                          <dt>
                            {g.label}
                            <span className="op-badge">
                              {stateText(g.state)}
                            </span>
                          </dt>
                          {!data.researchBoard ? (
                            <dd>{g.reason}</dd>
                          ) : (
                            !gateCovered(g) && (
                              <dd>
                                <button
                                  type="button"
                                  aria-expanded={expandedReasons.includes(g.id)}
                                  onClick={() => toggleReason(g.id)}
                                >
                                  查看{g.label}判断依据
                                </button>
                                {expandedReasons.includes(g.id) && (
                                  <p>{g.reason}</p>
                                )}
                              </dd>
                            )
                          )}
                        </div>
                      ))}
                  </dl>
                  {!decision.gates.some((g) => g.state === state) && (
                    <p>未列出此类条件，不等同于全部资格已通过。</p>
                  )}
                </section>
              ))}
              <p>策略验证：{stateText(decision.validation.state)}</p>
              {(!data.researchBoard ||
                !covered(decision.validation.summary)) && (
                <p>{decision.validation.summary}</p>
              )}
              <small>验证方法：{decision.validation.methodVersion}</small>
              {decision.validation.limitations?.map((reason) => (
                <p key={reason}>{reason}</p>
              ))}
              {!data.researchBoard && <h3>下一步核查</h3>}
              {!data.researchBoard &&
                decision.nextChecks.map((check) => <p key={check}>{check}</p>)}
            </>
          )}
          {portfolio && (
            <div className="op-radar-portfolio">
              {!data.researchBoard && <p>{portfolio.summary}</p>}
              <p>
                持仓状态：
                {portfolio.heldSelected == null
                  ? "待确认"
                  : portfolio.heldSelected
                    ? "已持有当前标的"
                    : "未持有"}
              </p>
              <p>
                当前标的权重：{percent(portfolio.selectedWeight)} · 同指数敞口：
                {percent(portfolio.sameIndexWeight)} · 现金比例：
                {percent(portfolio.cashRatio)}
              </p>
              <p>持仓快照：{timeLabel(portfolio.asOf)}</p>
              {!data.researchBoard && !!portfolio.missingEvidence.length && (
                <p>证据缺口：{portfolio.missingEvidence.join("、")}</p>
              )}
              {data.researchBoard &&
                (personalSummary || personalMissing.length > 0) && (
                  <div>
                    <button
                      type="button"
                      aria-expanded={expandedReasons.includes(
                        "personal-details",
                      )}
                      onClick={() => toggleReason("personal-details")}
                    >
                      查看个人持仓判断依据
                    </button>
                    {expandedReasons.includes("personal-details") && (
                      <>
                        {personalSummary && <p>{personalSummary}</p>}
                        {personalMissing.map((reason) => (
                          <p key={reason}>{reason}</p>
                        ))}
                      </>
                    )}
                  </div>
                )}
            </div>
          )}
        </section>
      )}
      {!hideTracking &&
        show("monitor") &&
        (radar || data.researchBoard || section === "monitor") && (
          <section className="op-radar-monitor" aria-label="站内研究条件跟踪">
            <h3>站内条件跟踪</h3>
            <p>
              显式启用后跟踪当前研究范围；首次建立基线，条件变化在站内显示。
            </p>
            <button
              disabled={!onMonitor || monitorPending || monitorLoading}
              onClick={() => onMonitor?.(!monitorEnabled)}
            >
              {monitorPending
                ? "保存中…"
                : monitorEnabled
                  ? "停止跟踪研究条件"
                  : "跟踪研究条件"}
            </button>
            {monitorError && <p role="alert">{monitorError}</p>}
            {!!data.notices?.length && (
              <div className="op-radar-notices">
                {data.notices.map((notice) => (
                  <article key={notice.id}>
                    <strong>{notice.title}</strong>
                    <p>{notice.summary}</p>
                    <small>
                      {timeLabel(notice.createdAt)} ·{" "}
                      <span>{notice.readAt ? "已读" : "未读"}</span>
                    </small>
                    {!notice.readAt && (
                      <button
                        aria-label={`标记${notice.title}为已读`}
                        disabled={!onNoticeAck || ackPending === notice.id}
                        onClick={() => onNoticeAck?.(notice.id)}
                      >
                        {ackPending === notice.id ? "保存中…" : "标记已读"}
                      </button>
                    )}
                  </article>
                ))}
              </div>
            )}
            {ackError && <p role="alert">{ackError}</p>}
          </section>
        )}
    </div>
  );
}

export function ResearchDirections({
  data,
  directions,
  selectedDirection,
  onSelect,
}: {
  data: OpportunityContext;
  directions: Direction[];
  selectedDirection?: Direction;
  onSelect?: (id: string) => void;
}) {
  const [all, setAll] = useState(false);
  const available = data.radar
    ? directions
    : directions.filter((d) => d.macroResearch);
  if (!available.length) return null;
  const readyEvidence = new Set([
    ...(data.series ?? [])
      .filter((s) => s.state === "READY" && finite(s.value))
      .map((s) => s.code),
    ...data.items
      .filter((i) => i.state === "READY" && i.valueVerification === "VERIFIED")
      .map((i) => i.code),
    ...(data.events ?? [])
      .filter((e) => e.sourceType === "OFFICIAL_FACT" && e.publishedAt)
      .map((e) => e.id),
  ]);
  const related = new Set(
    [data.radar?.headline, ...(data.radar?.changes ?? [])]
      .filter(
        (c): c is RadarChange =>
          !!c &&
          c.priority === "CRITICAL" &&
          c.evidenceIds.length > 0 &&
          c.evidenceIds.every((id) => readyEvidence.has(id)),
      )
      .flatMap((c) => c.relatedDirectionIds),
  );
  const extras = available
    .filter(
      (d) => d.marketId !== selectedDirection?.marketId && related.has(d.id),
    )
    .slice(0, 3);
  const visible =
    all || (!data.radar && !selectedDirection)
      ? available
      : !selectedDirection
        ? extras
        : available.filter(
            (d) =>
              d.marketId === selectedDirection.marketId ||
              d.id === selectedDirection.id ||
              extras.some((extra) => extra.id === d.id),
          );
  return (
    <section className="op-macro-directions" aria-label="宏观方向研究">
      <h3>沿影响渠道继续研究</h3>
      <div>
        {visible.map((d) => (
          <button
            key={d.id}
            aria-pressed={selectedDirection?.id === d.id}
            disabled={!onSelect}
            onClick={() => onSelect?.(d.id)}
          >
            研究 {d.marketLabel} · {d.label}
          </button>
        ))}
      </div>
      {available.some((d) => !visible.includes(d)) || all ? (
        <button aria-expanded={all} onClick={() => setAll(!all)}>
          {all ? "收起其他方向" : "全部研究方向"}
        </button>
      ) : null}
      <p>继续核对指数估值、盈利与基金质量，宏观变化不会自动改变基金排名。</p>
    </section>
  );
}

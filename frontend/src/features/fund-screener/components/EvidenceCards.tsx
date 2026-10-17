import { useState } from "react";
import type { OpportunitySeries } from "../../../api/investment";
import {
  changeLabel,
  macroUnit,
  metricValue,
  monthLabel,
  sourceHref,
  timeLabel,
  valueVerificationLabels,
  type MacroItem,
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
};
const stateText = (state?: string) => states[state ?? ""] ?? "待核查";
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const number = (value: unknown) => (finite(value) ? value.toFixed(2) : "—");
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

function HistoricalTrend({ item }: { item: MacroItem }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [plotRef, chart] = useEvidenceChart();
  const supplied = item.history ?? [];
  const history = supplied.filter(
    (p) =>
      Number.isFinite(p.value) &&
      Number.isFinite(Date.parse(p.asOf)) &&
      !["CONFLICT", "UNAVAILABLE"].includes(p.state ?? ""),
  );
  if (!supplied.length) return <p>历史序列尚未采集，暂不绘制走势。</p>;
  if (history.length < 2) {
    const reasons = [
      supplied.some((point) => !Number.isFinite(point.value)) && "观察数值无效",
      supplied.some((point) => !Number.isFinite(Date.parse(point.asOf))) &&
        "观察日期无效",
      supplied.some((point) =>
        ["CONFLICT", "UNAVAILABLE"].includes(point.state ?? ""),
      ) && "已排除冲突或不可用观察",
    ].filter(Boolean);
    return (
      <p>
        历史样本不足，暂不绘制走势。
        {reasons.length ? reasons.join("；") : "至少需要两个有效实际样本。"}
      </p>
    );
  }
  if (
    new Set(
      history.map((p) =>
        macroUnit({ code: item.code, unit: p.unit ?? item.unit }),
      ),
    ).size > 1
  )
    return <p>历史单位发生变化，暂不绘制可比较走势。</p>;
  const benchmark =
    item.benchmark && Number.isFinite(item.benchmark.value)
      ? item.benchmark
      : null;
  const values = [
    ...history.map((p) => p.value),
    ...(benchmark ? [benchmark.value] : []),
  ];
  const low = Math.min(...values),
    high = Math.max(...values);
  const padding = Math.max((high - low) * 0.1, 0.1);
  const yLow = low - padding,
    yHigh = high + padding;
  const first = Date.parse(history[0].asOf),
    last = Date.parse(history.at(-1)!.asOf);
  const y = (value: number) =>
    chart.bottom -
    ((value - yLow) / (yHigh - yLow)) * (chart.bottom - chart.top);
  const points = history.map((p) => ({
    ...p,
    x:
      chart.left +
      ((Date.parse(p.asOf) - first) / (last - first || 1)) *
        (chart.right - chart.left),
    y: y(p.value),
    month: Number(p.asOf.slice(0, 4)) * 12 + Number(p.asOf.slice(5, 7)),
    sourceKey: p.sourceUri ?? p.source ?? item.sourceUri ?? item.source,
  }));
  const active = points.find((p) => p.asOf === selected);
  return (
    <figure ref={plotRef} className="op-macro-chart op-evidence-plot">
      <svg
        role="img"
        aria-label={`${item.label} 历史走势`}
        viewBox={`0 0 ${chart.width} ${chart.height}`}
        width="100%"
        height={chart.height}
      >
        <title>{item.label} 实际历史样本，缺失月份不连接</title>
        <text x={4} y={15}>
          {macroUnit(item)}
        </text>
        {[yLow, (yLow + yHigh) / 2, yHigh].map((tick) => (
          <g key={tick}>
            <text x={chart.left - 6} y={y(tick) + 4} textAnchor="end">
              {tick.toFixed(1)}
            </text>
            <path
              d={`M${chart.left} ${y(tick)} H${chart.right}`}
              stroke="#e2e8f0"
              fill="none"
            />
          </g>
        ))}
        {benchmark && (
          <>
            <line
              aria-label={`${benchmark.label}：${benchmark.value} ${macroUnit(item)}`}
              x1={chart.left}
              x2={chart.right}
              y1={y(benchmark.value)}
              y2={y(benchmark.value)}
              stroke="#64748b"
              strokeDasharray="4 3"
            />
            <text x={chart.right} y={y(benchmark.value) - 5} textAnchor="end">
              {benchmark.label} {benchmark.value}
            </text>
          </>
        )}
        {points.map((point, i) => (
          <g key={point.asOf}>
            {i > 0 &&
              point.month - points[i - 1].month === 1 &&
              point.sourceKey === points[i - 1].sourceKey && (
                <line
                  x1={points[i - 1].x}
                  y1={points[i - 1].y}
                  x2={point.x}
                  y2={point.y}
                  stroke="#0d9488"
                  strokeWidth={2}
                />
              )}
            <circle
              role="button"
              tabIndex={0}
              aria-label={`查看${item.label} ${monthLabel(point.asOf)}`}
              cx={point.x}
              cy={point.y}
              r={4}
              fill="#0d9488"
              onMouseEnter={() => setSelected(point.asOf)}
              onFocus={() => setSelected(point.asOf)}
              onClick={() => setSelected(point.asOf)}
              onKeyDown={(event) => {
                if (["Enter", " "].includes(event.key)) {
                  event.preventDefault();
                  setSelected(point.asOf);
                }
              }}
            />
          </g>
        ))}
        <text x={chart.left} y={chart.height - 14}>
          {monthLabel(history[0].asOf)}
        </text>
        <text x={chart.right} y={chart.height - 14} textAnchor="end">
          {monthLabel(history.at(-1)!.asOf)}
        </text>
      </svg>
      <figcaption>
        {monthLabel(history[0].asOf)} 至 {monthLabel(history.at(-1)!.asOf)} ·
        取值范围：
        {Math.min(...history.map((p) => p.value)).toFixed(2)}–
        {Math.max(...history.map((p) => p.value)).toFixed(2)} {macroUnit(item)}
      </figcaption>
      <p
        className="op-macro-point"
        role="status"
        aria-label={`${item.label} 历史明细`}
        aria-live="polite"
      >
        {active ? (
          <>
            {monthLabel(active.asOf)}：{metricValue(active.value)}{" "}
            {macroUnit(item)} · 来源：
            {active.source ?? item.source ?? "未确认"}
            {active.revision != null && ` · 修订 ${active.revision}`}
            {active.publishedAt && ` · 公布：${timeLabel(active.publishedAt)}`}
          </>
        ) : (
          "悬停或键盘聚焦观察值，查看月份、数值与来源。"
        )}
      </p>
    </figure>
  );
}

export function EvidenceCard({ item }: { item: MacroItem }) {
  const [open, setOpen] = useState(false);
  const unit = macroUnit(item),
    stats = item.statistics;
  const baseUrl = sourceHref(item.sourceUri),
    officialUrl = sourceHref(item.officialEvidence?.sourceUri);
  const scheduleUrl = sourceHref(item.nextRelease?.sourceUri);
  const publicationConfirmed =
    item.publicationStatus === "CONFIRMED" ||
    (!item.publicationStatus && !!item.publishedAt);
  return (
    <article className="op-macro-card op-evidence-card">
      <strong>{item.label}</strong>
      <div className="op-evidence-layout">
        <HistoricalTrend item={item} />
        <div className="op-evidence-facts">
          <h3>
            {item.value == null || !Number.isFinite(item.value)
              ? "—"
              : `${metricValue(item.value, item.code === "PMI" ? 1 : 2)} ${unit}`.trim()}
          </h3>
          <p>连续月变化：{changeLabel(item.periodChange, item)}</p>
          {item.benchmark &&
            Number.isFinite(item.benchmark.value) &&
            item.value != null &&
            Number.isFinite(item.value) && (
              <p>
                {item.benchmark.label}：{metricValue(item.benchmark.value)}{" "}
                {unit} · 相差：
                {changeLabel(item.value - item.benchmark.value, item)}
              </p>
            )}
          <p className="op-macro-interpretation">
            {item.interpretation?.summary ?? "已采集事实，经济含义仍需核查。"}
          </p>
          <p>数据所属月份：{monthLabel(item.referencePeriod ?? item.asOf)}</p>
          <p
            className={`op-macro-state op-macro-state-${item.valueVerification ?? item.state}`}
          >
            {valueVerificationLabels[item.valueVerification ?? item.state] ??
              "数值来源待核对"}
          </p>
          {item.state === "STALE" && item.valueVerification && (
            <p>旧期证据，需更新</p>
          )}
          {stats && (
            <div className="op-macro-statistics">
              <p>近3月变化：{changeLabel(stats.change3Months, item)}</p>
              <p>近6月变化：{changeLabel(stats.change6Months, item)}</p>
              <p>
                历史分位：
                {stats.percentile == null || !Number.isFinite(stats.percentile)
                  ? "暂不可计算"
                  : `${stats.percentile.toFixed(2)}%`}
              </p>
            </div>
          )}
          {item.trendState === "GAP" && (
            <p>历史月份有缺口或口径变化，仅展示实际样本。</p>
          )}
          {item.trendState === "INSUFFICIENT" && (
            <p>历史样本不足，暂不计算变化。</p>
          )}
        </div>
      </div>
      <details className="op-evidence-metadata" open={open}>
        <summary
          role="button"
          aria-expanded={open}
          onClick={(event) => {
            event.preventDefault();
            setOpen(!open);
          }}
        >
          {open ? "收起" : "查看"}
          {item.label} 数据依据
        </summary>
        <p>
          {publicationConfirmed
            ? `官方实际公布：${timeLabel(item.officialEvidence?.publishedAt ?? item.publishedAt)}`
            : "发布时间待确认：公布时间未核实，仅展示观察期事实。"}
        </p>
        {item.nextRelease?.scheduledAt &&
        item.nextRelease.status !== "UNKNOWN" ? (
          <p>
            下期{monthLabel(item.nextRelease.referencePeriod)}：
            {timeLabel(item.nextRelease.scheduledAt)} ·{" "}
            {item.nextRelease.status === "OVERDUE"
              ? "已过计划，待公告"
              : "计划，可能调整"}
            {scheduleUrl && (
              <>
                {" "}
                ·{" "}
                <a href={scheduleUrl} target="_blank" rel="noreferrer">
                  发布日程来源
                </a>
              </>
            )}
          </p>
        ) : (
          <p>暂无已核实的下期发布日程。</p>
        )}
        <p>
          已采集 {item.sampleCount ?? item.history?.length ?? 0} 个历史样本 ·{" "}
          {item.historyMonths ?? 36} 个月窗口
        </p>
        {stats && (
          <div className="op-macro-statistics">
            <p>
              统计基于 {stats.sampleCount} 个实际月样本 ·{" "}
              {monthLabel(stats.firstPeriod)} 至 {monthLabel(stats.lastPeriod)}
            </p>
            {!!stats.missingPeriods.length && (
              <p>月份间隔：{stats.missingPeriods.map(monthLabel).join("、")}</p>
            )}
          </div>
        )}
        <div className="op-macro-evidence">
          <p>
            基础来源：{item.source ?? "未确认"}
            {baseUrl && (
              <>
                {" "}
                ·{" "}
                <a href={baseUrl} target="_blank" rel="noreferrer">
                  基础数据来源
                </a>
              </>
            )}
          </p>
          {item.officialEvidence ? (
            <>
              <p>
                官方核验：{item.officialEvidence.publisher} · 数值：
                {metricValue(item.officialEvidence.value)} {unit}
              </p>
              {officialUrl && (
                <a href={officialUrl} target="_blank" rel="noreferrer">
                  官方核验来源
                </a>
              )}
              <p>
                官方证据获取：{timeLabel(item.officialEvidence.retrievedAt)}
              </p>
            </>
          ) : (
            <p>尚无可核对的同期官方数值证据。</p>
          )}
          <p>方法：{item.interpretation?.methodVersion ?? "未提供"}</p>
          {item.interpretation?.limitations.map((reason) => (
            <p key={reason}>{reason}</p>
          ))}
        </div>
      </details>
    </article>
  );
}

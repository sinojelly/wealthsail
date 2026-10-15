import { useState } from "react";
import { Globe2, ChevronDown } from "lucide-react";
import type {
  Direction,
  OpportunityContext,
  MacroEvent,
} from "../../../api/investment";
import {
  changeLabel,
  macroUnit,
  monthLabel,
  sourceHref,
  timeLabel,
  valueVerificationLabels,
  type MacroItem,
} from "../macro-display";
import {
  RadarOverview,
  RadarDetails,
  ResearchDirections,
} from "./OpportunityRadar";
import ResearchBoard from "./ResearchBoard";

function HistoricalTrend({ item }: { item: MacroItem }) {
  const [selected, setSelected] = useState<string | null>(null);
  const history = (item.history ?? []).filter(
    (p) =>
      Number.isFinite(p.value) &&
      Number.isFinite(Date.parse(p.asOf)) &&
      !["CONFLICT", "UNAVAILABLE"].includes(p.state ?? ""),
  );
  if (!history.length) return <p>历史序列尚未采集，暂不绘制走势。</p>;
  if (history.length < 2) return <p>历史样本不足，暂不绘制走势。</p>;
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
  const y = (value: number) => 140 - ((value - yLow) / (yHigh - yLow)) * 110;
  const points = history.map((p) => ({
    ...p,
    x: 48 + ((Date.parse(p.asOf) - first) / (last - first || 1)) * 292,
    y: y(p.value),
    month: Number(p.asOf.slice(0, 4)) * 12 + Number(p.asOf.slice(5, 7)),
    sourceKey: p.sourceUri ?? p.source ?? item.sourceUri ?? item.source,
  }));
  const active = points.find((p) => p.asOf === selected);
  return (
    <figure className="op-macro-chart">
      <svg
        role="img"
        aria-label={`${item.label} 历史走势`}
        viewBox="0 0 360 180"
        width="100%"
        height={180}
      >
        <title>{item.label} 实际历史样本，缺失月份不连接</title>
        <text x={4} y={15}>
          {macroUnit(item)}
        </text>
        {[yLow, (yLow + yHigh) / 2, yHigh].map((tick) => (
          <g key={tick}>
            <text x={42} y={y(tick) + 4} textAnchor="end">
              {tick.toFixed(1)}
            </text>
            <path d={`M48 ${y(tick)} H340`} stroke="#e2e8f0" fill="none" />
          </g>
        ))}
        {benchmark && (
          <>
            <line
              aria-label={`${benchmark.label}：${benchmark.value} ${macroUnit(item)}`}
              x1={48}
              x2={340}
              y1={y(benchmark.value)}
              y2={y(benchmark.value)}
              stroke="#64748b"
              strokeDasharray="4 3"
            />
            <text x={340} y={y(benchmark.value) - 5} textAnchor="end">
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
        <text x={48} y={166}>
          {monthLabel(history[0].asOf)}
        </text>
        <text x={340} y={166} textAnchor="end">
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
            {monthLabel(active.asOf)}：{active.value} {macroUnit(item)} · 来源：
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

function EvidenceCard({ item }: { item: MacroItem }) {
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
    <article className="op-macro-card">
      <strong>{item.label}</strong>
      <h3>
        {item.value == null || !Number.isFinite(item.value)
          ? "—"
          : `${item.value} ${unit}`.trim()}
      </h3>
      <p>连续月变化：{changeLabel(item.periodChange, item)}</p>
      {item.benchmark && item.value != null && Number.isFinite(item.value) && (
        <p>
          {item.benchmark.label}：{item.benchmark.value} {unit} · 相差：
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
      <HistoricalTrend item={item} />
      <p>
        已采集 {item.sampleCount ?? item.history?.length ?? 0} 个历史样本 ·{" "}
        {item.historyMonths ?? 36} 个月窗口
      </p>
      {item.trendState === "GAP" && (
        <p>历史月份有缺口或口径变化，仅展示实际样本。</p>
      )}
      {item.trendState === "INSUFFICIENT" && (
        <p>历史样本不足，暂不计算变化。</p>
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
          <p>
            统计基于 {stats.sampleCount} 个实际月样本 ·{" "}
            {monthLabel(stats.firstPeriod)} 至 {monthLabel(stats.lastPeriod)}
          </p>
          {!!stats.missingPeriods.length && (
            <p>月份间隔：{stats.missingPeriods.map(monthLabel).join("、")}</p>
          )}
        </div>
      )}
      <button
        className="op-macro-evidence-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {open ? "收起" : "查看"}
        {item.label} 数据依据
      </button>
      {open && (
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
                {item.officialEvidence.value ?? "—"} {unit}
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
      )}
    </article>
  );
}

function MacroEvents({
  events,
  state,
}: {
  events: MacroEvent[];
  state?: string;
}) {
  const [all, setAll] = useState(false);
  return (
    <section className="op-macro-events" aria-label="官方关键事件">
      <h3>官方关键事件</h3>
      {!events.length ? (
        <p>
          {state === "UNAVAILABLE"
            ? "官方事件来源暂不可用，稍后可重试。"
            : "暂无已核验事件。"}
        </p>
      ) : (
        (all ? events : events.slice(0, 3)).map((event) => (
          <article key={event.id}>
            <strong>
              {sourceHref(event.sourceUri) ? (
                <a
                  href={sourceHref(event.sourceUri)}
                  target="_blank"
                  rel="noreferrer"
                >
                  {event.title}
                </a>
              ) : (
                event.title
              )}
            </strong>
            <p>
              {event.publisher} ·{" "}
              {event.sourceType === "OFFICIAL_FACT"
                ? "官方事实"
                : "来源类型待核实"}{" "}
              ·{" "}
              {event.publishedAt
                ? `公布：${timeLabel(event.publishedAt)}`
                : "实际发布时间待确认"}
            </p>
            <p>{event.summary}</p>
          </article>
        ))
      )}
      {events.length > 3 && (
        <button onClick={() => setAll(!all)} aria-expanded={all}>
          {all ? "收起事件" : `查看全部 ${events.length} 条事件`}
        </button>
      )}
    </section>
  );
}

export default function MacroContextStrip({
  data,
  error,
  loading = false,
  months = 36,
  onMonthsChange,
  directions = [],
  onDirectionSelect,
  selectedDirection,
  onMonitor,
  monitorEnabled,
  monitorPending,
  monitorLoading,
  monitorError,
  onNoticeAck,
  ackPending,
  ackError,
  canViewOperations = false,
}: {
  data?: OpportunityContext;
  error: boolean;
  loading?: boolean;
  months?: number;
  onMonthsChange?: (months: number) => void;
  directions?: Direction[];
  onDirectionSelect?: (directionId: string) => void;
  selectedDirection?: Direction;
  onMonitor?: (enabled: boolean) => void;
  monitorEnabled?: boolean;
  monitorPending?: boolean;
  monitorLoading?: boolean;
  monitorError?: string;
  onNoticeAck?: (id: string) => void;
  ackPending?: string | null;
  ackError?: string;
  canViewOperations?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const research = !error && !loading ? data?.research : null;
  const readyData = !error && !loading ? data : undefined;
  if (readyData?.researchBoard) {
    const domestic = new Set([
      "CN_T10",
      "DR007",
      "M1_YOY",
      "TSF_STOCK_YOY",
      "CORP_MEDLONG_LOANS_YTD",
    ]);
    const foreign = new Set([
      "US_T10_NOMINAL",
      "US_T10_REAL",
      "DXY",
      "USD_CNY",
    ]);
    const core = (readyData.series ?? []).filter((series) =>
      ["us", "gold"].includes(selectedDirection?.marketId ?? "")
        ? foreign.has(series.code)
        : selectedDirection
          ? domestic.has(series.code)
          : false,
    );
    const global = (readyData.series ?? []).filter(
      (series) => !core.includes(series),
    );
    const windowSelector = (
      <div className="op-macro-window">
        <label>
          宏观观察窗口{" "}
          <select
            aria-label="宏观观察窗口"
            value={months}
            onChange={(event) => onMonthsChange?.(Number(event.target.value))}
          >
            {[12, 36, 60].map((window) => (
              <option key={window} value={window}>
                {window} 个月
              </option>
            ))}
          </select>
        </label>
        <span>图表窗口；研究统计采用固定历史样本。</span>
      </div>
    );
    return (
      <ResearchBoard
        data={readyData}
        canViewOperations={canViewOperations}
        directionLabel={
          selectedDirection
            ? `${selectedDirection.marketLabel} · ${selectedDirection.label}`
            : undefined
        }
        trackingEnabled={monitorEnabled}
        onTrack={
          onMonitor && !monitorEnabled && !monitorPending && !monitorLoading
            ? () => onMonitor(true)
            : undefined
        }
        topics={[
          {
            id: "research-macro",
            label: "宏观与流动性",
            render: () => (
              <>
                {windowSelector}
                <div className="op-macro-grid">
                  {readyData.items.map((item) => (
                    <EvidenceCard
                      key={item.code}
                      item={{
                        ...item,
                        historyMonths:
                          item.historyMonths ?? readyData.months ?? months,
                      }}
                    />
                  ))}
                </div>
                <RadarDetails
                  data={{ ...readyData, series: core }}
                  section="series"
                />
              </>
            ),
          },
          {
            id: "research-market",
            label: "指数与基金",
            render: () => (
              <>
                <RadarDetails data={readyData} section="market" />
                <p className="op-muted">
                  基金费用、跟踪质量及产品证据可在下方选择基金后查看研究诊断。
                </p>
              </>
            ),
          },
          {
            id: "research-global",
            label: "全球市场与事件",
            render: () => (
              <>
                <RadarDetails
                  data={{ ...readyData, series: global }}
                  section="series"
                />
                <MacroEvents
                  events={readyData.events ?? []}
                  state={readyData.eventState}
                />
              </>
            ),
          },
          {
            id: "research-strategy",
            label: "策略与持仓",
            render: () => <RadarDetails data={readyData} section="strategy" />,
          },
        ]}
        renderTracking={() => (
          <RadarDetails
            data={readyData}
            section="monitor"
            onMonitor={onMonitor}
            monitorEnabled={monitorEnabled}
            monitorPending={monitorPending}
            monitorLoading={monitorLoading}
            monitorError={monitorError}
            onNoticeAck={onNoticeAck}
            ackPending={ackPending}
            ackError={ackError}
          />
        )}
      />
    );
  }
  return (
    <section className="op-panel op-macro-research">
      <button
        className="op-macro-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span>
          <Globe2 size={19} />
          <strong>市场环境概览</strong>
          <span className="op-muted">增长 · 通胀 · 流动性 · 风险</span>
        </span>
        <span>
          <span className="op-badge">
            {error
              ? "读取失败"
              : loading
                ? "加载中"
                : readyData?.state === "READY"
                  ? "事实已更新"
                  : "证据待核对"}
          </span>
          <ChevronDown size={16} />
        </span>
      </button>
      <div className="op-macro-overview">
        {error ? (
          <p role="alert">宏观数据读取失败，请稍后重试。</p>
        ) : loading ? (
          <p role="status">宏观数据加载中…</p>
        ) : readyData?.radar ? (
          <RadarOverview
            data={readyData}
            selectedDirection={selectedDirection}
          />
        ) : (
          <>
            <p>
              {research?.summary ??
                (readyData?.items.length
                  ? "已采集宏观事实，解释与核验信息待更新。"
                  : "宏观数据尚未接入。方向探索和基金搜索仍可使用。")}
            </p>
            {!!readyData?.items.length && (
              <div className="op-macro-latest">
                {readyData.items.map((item) => (
                  <span key={item.code}>
                    {item.label}{" "}
                    {item.value == null || !Number.isFinite(item.value)
                      ? "—"
                      : `${item.value} ${macroUnit(item)}`.trim()}
                  </span>
                ))}
              </div>
            )}
          </>
        )}
      </div>
      {open && readyData && (
        <>
          <RadarDetails
            data={readyData}
            onMonitor={onMonitor}
            monitorEnabled={monitorEnabled}
            monitorPending={monitorPending}
            monitorLoading={monitorLoading}
            monitorError={monitorError}
            onNoticeAck={onNoticeAck}
            ackPending={ackPending}
            ackError={ackError}
          />
          <div className="op-macro-window">
            <label>
              宏观观察窗口{" "}
              <select
                aria-label="宏观观察窗口"
                value={months}
                onChange={(event) =>
                  onMonthsChange?.(Number(event.target.value))
                }
              >
                {[12, 36, 60].map((window) => (
                  <option key={window} value={window}>
                    {window} 个月
                  </option>
                ))}
              </select>
            </label>
            <span>图表窗口；研究统计采用固定历史样本。</span>
          </div>
          {research && (
            <section className="op-macro-dimensions" aria-label="四维宏观研究">
              {research.dimensions.map((dimension) => (
                <article key={dimension.id}>
                  <strong>{dimension.label}</strong>
                  <span className="op-badge">
                    {{
                      PARTIAL: "部分覆盖",
                      INSUFFICIENT: "证据不足",
                      CONFLICT: "证据冲突",
                    }[dimension.state] ?? "待研究"}
                  </span>
                  <p>{dimension.summary}</p>
                  {!!dimension.evidenceCodes.length && (
                    <p>依据：{dimension.evidenceCodes.join("、")}</p>
                  )}
                  {!!dimension.missingEvidence.length && (
                    <p>待核查：{dimension.missingEvidence.join("、")}</p>
                  )}
                </article>
              ))}
            </section>
          )}
          <div className="op-macro-grid">
            {readyData.items.map((item) => (
              <EvidenceCard
                key={item.code}
                item={{
                  ...item,
                  historyMonths: item.historyMonths ?? data?.months ?? months,
                }}
              />
            ))}
          </div>
          {research && (
            <section className="op-macro-changes" aria-label="关键变化与限制">
              <h3>关键变化与待核查事项</h3>
              {research.changes.map((change) => (
                <p key={change.code}>{change.summary}</p>
              ))}
              {research.limitations.map((limit) => (
                <p key={limit}>{limit}</p>
              ))}
              <small>研究方法：{research.methodVersion}</small>
            </section>
          )}
          <MacroEvents
            events={readyData.events ?? []}
            state={readyData.eventState}
          />
          <ResearchDirections
            data={readyData}
            directions={directions}
            selectedDirection={selectedDirection}
            onSelect={onDirectionSelect}
          />
        </>
      )}
    </section>
  );
}

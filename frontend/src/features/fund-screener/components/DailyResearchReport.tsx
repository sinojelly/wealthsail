import { useRef, useState, type ReactNode } from "react";
import type { DailyResearch, Direction } from "../../../api/investment";
import { sourceHref, timeLabel } from "../macro-display";
import { readingText } from "../research-reading";
const stateLabels: Record<string, string> = {
  NOT_RUN: "今日尚未研究",
  RUNNING: "已登记每日研究",
  READY: "日报已完成",
  STALE: "历史日报",
  FAILED: "今日研究失败",
  UNCERTAIN: "执行结果待确认",
  DISABLED: "每日研究未启用",
};
const phases: Record<string, string> = {
  PLANNED: "计划中",
  ANNOUNCED: "已宣布",
  IMPLEMENTED: "已实施",
  REPORTED: "报道线索",
  OPINION: "观点",
};
const topics: Record<string, string> = {
  DOMESTIC_GROWTH: "国内增长与信用",
  US_RATES: "美国通胀与利率",
  ENERGY_SHIPPING: "能源与航运",
  GEOPOLITICS_POLICY: "全球政策与地缘事件",
  GLOBAL_EQUITIES: "全球权益",
  CALENDAR: "未来日历",
};
const energyNumber = (value: number | null) =>
  value != null && Number.isFinite(value) ? value.toFixed(3) : "—";
const energyUnit = (unit: string) =>
  unit === "USD/barrel"
    ? "美元/桶"
    : unit === "USD/gallon"
      ? "美元/加仑"
      : unit;
const dailyStateLabel = (state?: string, hasReport?: boolean) =>
  state === "STALE" && hasReport === false
    ? "暂无有效日报"
    : (stateLabels[state ?? ""] ?? "已有事实观察");
const scheduleLabels: Record<string, string> = {
  NOT_DUE: "尚未到今日计划时间",
  DUE_NOT_DISPATCHED: "今日到期，尚未登记派发",
  DISPATCHED: "今日已登记派发",
  DISABLED: "每日研究未启用",
};
export function DailyResearchStatus({
  state,
  scheduleState,
  hasReport,
}: {
  state?: string;
  scheduleState?: string;
  hasReport?: boolean;
}) {
  return (
    <>
      {dailyStateLabel(state, hasReport)}
      {scheduleState && scheduleLabels[scheduleState]
        ? ` · ${scheduleLabels[scheduleState]}`
        : ""}
    </>
  );
}
export function EnergyObservations({
  rows = [],
}: {
  rows?: NonNullable<DailyResearch["report"]>["energy"];
}) {
  return rows.length ? (
    <section className="op-daily-energy" aria-label="能源事实">
      <h3>能源实际观察</h3>
      {rows.map((row) => (
        <article key={row.code}>
          <strong>{row.label}</strong>
          <p>
            {energyNumber(row.value)} {energyUnit(row.unit)}
          </p>
          <p>
            {row.frequency === "WEEKLY" ? "周度" : "日度"}实际观察 ·{" "}
            {row.state === "READY" ? "当前事实可用" : "历史事实，待更新"}
          </p>
          <p>
            前一观察：{timeLabel(row.previousDate)} ·{" "}
            {energyNumber(row.previousValue)}；相邻变化：
            {energyNumber(row.change)} {energyUnit(row.unit)}
          </p>
          <small>
            观察期：{timeLabel(row.asOf)} · 获取：{timeLabel(row.retrievedAt)}
          </small>
          <p>观察日期不代表已核实的公布时刻；能源价格观察不认证事件因果。</p>
          {sourceHref(row.sourceUri) && (
            <a
              href={sourceHref(row.sourceUri)}
              target="_blank"
              rel="noreferrer"
            >
              查看{row.label}来源
            </a>
          )}
        </article>
      ))}
    </section>
  ) : null;
}
interface DailyResearchReportProps {
  data?: DailyResearch | null;
  directions?: Direction[];
  onDirectionSelect?: (id: string) => void;
  onTrack?: (reportRunId: string, eventId: string, horizonDays: number) => void;
  trackingPending?: boolean;
  trackingError?: string;
  children?: ReactNode;
}

export default function DailyResearchReport(props: DailyResearchReportProps) {
  return (
    <DailyResearchContent
      key={props.data?.reportRunId ?? "no-report"}
      {...props}
    />
  );
}

function DailyResearchContent({
  data,
  directions = [],
  onDirectionSelect,
  children,
}: DailyResearchReportProps) {
  const [fullOpen, setFullOpen] = useState(false);
  const fullEventRefs = useRef(new Map<string, HTMLElement>());
  const report = data?.report;
  const events =
    report?.events.filter(
      (event, index, rows) =>
        rows.findIndex((row) => row.id === event.id) === index,
    ) ?? [];
  const checkedCount = (ids: string[]) =>
    [...new Set(ids)].filter((id) =>
      report?.sources.some(
        (source) =>
          source.id === id && source.verification === "SOURCE_CHECKED",
      ),
    ).length;
  const importance: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 };
  const priorityEvents = events
    .map((event, order) => ({ event, order }))
    .sort(
      (a, b) =>
        (importance[a.event.importance] ?? 3) -
          (importance[b.event.importance] ?? 3) ||
        checkedCount(b.event.sourceIds) - checkedCount(a.event.sourceIds) ||
        a.order - b.order,
    )
    .slice(0, 3)
    .map(({ event }) => event);
  const upcoming =
    report?.upcoming.filter(
      (event, index, rows) =>
        rows.findIndex((row) => row.id === event.id) === index,
    ) ?? [];
  const safeErrorClass =
    data?.errorClass && /^[A-Za-z][A-Za-z0-9_.]{0,79}$/.test(data.errorClass)
      ? data.errorClass
      : "未分类错误";
  function related(ids: string[]) {
    return [...new Set(ids)].flatMap((id) => {
      const direction = directions.find((row) => row.id === id);
      return direction
        ? [
            <button
              type="button"
              key={id}
              disabled={!onDirectionSelect}
              onClick={() => onDirectionSelect?.(id)}
            >
              研究 {direction.marketLabel} · {direction.label}
            </button>,
          ]
        : [];
    });
  }
  function sources(ids: string[]) {
    return (
      <section aria-label="本事项原始来源">
        <h4>原始来源与核对</h4>
        {!ids.length && <p>未提供原始来源，仍需人工核查。</p>}
        {[...new Set(ids)].map((id) => {
          const source = report?.sources.find((row) => row.id === id);
          return source ? (
            <div key={id}>
              {sourceHref(source.url) ? (
                <a
                  href={sourceHref(source.url)}
                  target="_blank"
                  rel="noreferrer"
                >
                  {source.title}
                </a>
              ) : (
                <strong>{source.title}</strong>
              )}
              <p>
                {source.verification === "SOURCE_CHECKED"
                  ? "原文已核对，仅证明引用匹配，不代表影响判断已核实。"
                  : source.verification === "REPORTED"
                    ? "报道线索，原文与事实仍待核查。"
                    : "来源尚未完成核对。"}
              </p>
              <blockquote>{source.quote}</blockquote>
              <small>
                公布：{timeLabel(source.publishedAt ?? source.publishedDate)} ·
                获取：{timeLabel(source.retrievedAt)}
              </small>
            </div>
          ) : (
            <p key={id}>引用待核查，尚无可展示的原文。</p>
          );
        })}
      </section>
    );
  }
  function calendar(rows: typeof upcoming) {
    return (
      <ul>
        {rows.map((event) => (
          <li key={event.id}>
            <strong>{event.title}</strong>
            <p>
              {timeLabel(event.scheduledAt ?? event.scheduledDate)} ·
              计划，尚未发生
            </p>
            <p className="op-daily-next">
              下一核查：{event.watchFor[0] ?? "未声明，待实际公布后核对。"}
            </p>
            {!!event.sourceIds.length && (
              <details>
                <summary>查看日程依据</summary>
                {sources(event.sourceIds)}
              </details>
            )}
          </li>
        ))}
      </ul>
    );
  }
  function viewEvent(id: string) {
    setFullOpen(true);
    requestAnimationFrame(() => {
      const target = fullEventRefs.current.get(id);
      target?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      target?.focus({ preventScroll: true });
    });
  }
  function affectedDirections(ids: string[]) {
    return (
      [
        ...new Set(
          ids.map((id) => {
            const direction = directions.find((row) => row.id === id);
            return direction
              ? `${direction.marketLabel} · ${direction.label}`
              : "待核对";
          }),
        ),
      ].join("、") || "未声明"
    );
  }
  function impactReading(event: (typeof events)[number]) {
    const scenario = event.scenarios.find((row) => row.condition && row.impact);
    const channel = event.channels[0];
    return scenario
      ? [
          channel && readingText(channel, 40),
          `条件：${readingText(scenario.condition, 60)}`,
          `影响：${readingText(scenario.impact, 90)}`,
        ]
          .filter(Boolean)
          .join("；")
      : readingText(channel || "尚未声明传导假设，需人工核查。");
  }
  return (
    <section className="op-daily-report" aria-label="每日事件研究">
      <h3>{report ? "关键经济要闻" : "暂无有效日报"}</h3>
      {report && <p>报告证据时间：{timeLabel(report.asOf ?? data?.asOf)}</p>}
      {!report ? (
        <>
          <p>有效报告尚未生成，可先核查市场证据。</p>
          {children}
        </>
      ) : (
        <>
          {data?.state !== "READY" && (
            <p role="status">
              当前显示历史报告及原始日期，不代表今日已完成研究。
            </p>
          )}
          <ul className="op-daily-events" aria-label="重要事件">
            {priorityEvents.map((event) => (
              <li key={event.id}>
                <article className="op-daily-event">
                  <header>
                    <h3>{readingText(event.title, 64)}</h3>
                  </header>
                  <div className="op-event-reading">
                    <p>
                      事实：
                      {readingText(
                        event.summary || "尚未提供事件事实，需人工核查。",
                      )}
                    </p>
                    <p>
                      潜在影响：
                      {impactReading(event)}
                    </p>
                    <p className="op-daily-affected">
                      受影响方向：
                      {readingText(
                        affectedDirections(event.affectedDirectionIds),
                        100,
                      )}
                    </p>
                  </div>
                  <div className="op-research-actions">
                    <button
                      type="button"
                      aria-label={`查看${event.title}事件依据与情景`}
                      onClick={() => viewEvent(event.id)}
                    >
                      展开详情与原文依据
                    </button>
                  </div>
                </article>
              </li>
            ))}
          </ul>
          {!priorityEvents.length && (
            <p>本报告尚无可展示的重要事件；可继续核查当前资产与完整报告。</p>
          )}
          {children}
          {!!upcoming.length && (
            <section className="op-daily-calendar" aria-label="未来关键事件">
              <details>
                <summary>接下来关注什么 · {upcoming.length}项日程</summary>
                <h3>未来关键事件</h3>
                {calendar(upcoming)}
              </details>
            </section>
          )}
          <details
            className="op-daily-full-report"
            open={fullOpen}
            onToggle={(event) => setFullOpen(event.currentTarget.open)}
          >
            <summary>阅读完整日报</summary>
            <h4>整体研究摘要</h4>
            <p>{readingText(report.summary, 1500)}</p>
            <ul className="op-daily-events" aria-label="完整日报事件">
              {events.map((event) => (
                <li key={event.id}>
                  <article
                    className="op-daily-event"
                    tabIndex={-1}
                    ref={(element) => {
                      if (element) fullEventRefs.current.set(event.id, element);
                      else fullEventRefs.current.delete(event.id);
                    }}
                  >
                    <header>
                      <h3>{readingText(event.title, 200)}</h3>
                    </header>
                    <p>{readingText(event.summary, 5000)}</p>
                    <p className="op-daily-affected">
                      受影响方向：
                      {affectedDirections(event.affectedDirectionIds)}
                    </p>
                    <p className="op-daily-next">
                      接下来核查：
                      {event.watchFor[0] ?? "未声明下一条件，需人工核查。"}
                    </p>
                    <div className="op-daily-event-details">
                      {!!event.affectedDirectionIds.length && (
                        <div className="op-research-actions">
                          {related(event.affectedDirectionIds)}
                        </div>
                      )}
                      <p>
                        实际阶段：
                        <span className="op-badge">
                          {phases[event.phase] ?? "阶段待核查"}
                        </span>
                      </p>
                      <small>事件事实尚未独立核验；影响为模型条件推演。</small>
                      {!!event.channels.length && (
                        <section>
                          <h4>可能传导 · 模型条件推演</h4>
                          {event.channels.map((channel, i) => (
                            <p key={i}>{readingText(channel, 1200)}</p>
                          ))}
                        </section>
                      )}
                      {!!event.scenarios.length && (
                        <section>
                          <h4>正反情景与验证条件</h4>
                          {event.scenarios.map((scenario, i) => (
                            <div key={i}>
                              <strong>
                                若：{readingText(scenario.condition, 1000)}
                              </strong>
                              <p>
                                可能影响：{readingText(scenario.impact, 1000)}
                              </p>
                              <p>
                                反方条件：{readingText(scenario.counter, 1000)}
                              </p>
                            </div>
                          ))}
                        </section>
                      )}
                      <p>
                        市场反应：尚未核验；价格同时变化不能直接认定事件因果。
                      </p>
                      {event.watchFor.length > 1 && (
                        <section>
                          <h4>其他核查条件</h4>
                          {event.watchFor.slice(1).map((condition) => (
                            <p key={condition}>
                              {readingText(condition, 1000)}
                            </p>
                          ))}
                        </section>
                      )}
                      {!!event.unknowns.length && (
                        <section>
                          <h4>尚未确认</h4>
                          {event.unknowns.map((unknown) => (
                            <p key={unknown}>{readingText(unknown, 1000)}</p>
                          ))}
                        </section>
                      )}
                      {sources(event.sourceIds)}
                    </div>
                  </article>
                </li>
              ))}
            </ul>
            {!!report.coverage.length && (
              <section aria-label="研究覆盖与未找到主题">
                <h4>研究覆盖与未找到主题</h4>
                {report.coverage.map((item) => (
                  <p key={item.topic}>
                    <strong>{topics[item.topic] ?? "其他研究主题"}</strong> ·{" "}
                    {item.status === "FOUND"
                      ? "已找到线索"
                      : item.status === "NOT_FOUND"
                        ? "未找到充分线索"
                        : "待核查"}
                    ：{item.summary}
                  </p>
                ))}
                <small>
                  未找到线索不代表不存在重要事件，日报不承诺完整覆盖。
                </small>
              </section>
            )}
          </details>
        </>
      )}
      {data && (
        <details className="op-daily-run">
          <summary>执行与报告状态</summary>
          <p>
            本次任务：{data.runId ?? "未登记"} ·{" "}
            <DailyResearchStatus
              state={data.state}
              scheduleState={data.scheduleState}
              hasReport={!!report}
            />
          </p>
          <p>
            本次计划：{timeLabel(data.scheduledFor)} · 实际开始：
            {timeLabel(data.startedAt)} · 结束：{timeLabel(data.completedAt)}
          </p>
          {(data.state === "FAILED" || data.state === "UNCERTAIN") && (
            <p>
              安全错误类：<span>{safeErrorClass}</span>
            </p>
          )}
          {report && (
            <p>
              当前有效报告：{data.reportRunId ?? "身份未记录"} · 完成：
              {timeLabel(data.reportCompletedAt)} · 证据：
              {timeLabel(report.asOf ?? data.asOf)}
            </p>
          )}
          <small>
            下次计划：{timeLabel(data.nextRunAt)}
            ；计划与执行登记不代表研究服务此刻在线。
          </small>
        </details>
      )}
    </section>
  );
}

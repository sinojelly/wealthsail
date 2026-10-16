import type { DailyResearch, Direction } from "../../../api/investment";
import { sourceHref, timeLabel } from "../macro-display";
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
const dailyStateLabel = (state?: string) =>
  stateLabels[state ?? ""] ?? "已有事实观察";
export function DailyResearchStatus({ state }: { state?: string }) {
  return <>{dailyStateLabel(state)}</>;
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
export default function DailyResearchReport({
  data,
  directions = [],
  onDirectionSelect,
  onTrack,
  trackingPending = false,
}: {
  data?: DailyResearch | null;
  directions?: Direction[];
  onDirectionSelect?: (id: string) => void;
  onTrack?: (reportRunId: string, eventId: string) => void;
  trackingPending?: boolean;
}) {
  const report = data?.report;
  function related(ids: string[]) {
    return ids.flatMap((id) => {
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
  function refs(ids: string[]) {
    return (
      <small>
        依据：
        {ids
          .map(
            (id) =>
              report?.sources.find((source) => source.id === id)?.title ??
              "引用待核查",
          )
          .join("、")}
      </small>
    );
  }
  return (
    <section className="op-daily-report" aria-label="每日事件研究">
      <h3>{dailyStateLabel(data?.state)}</h3>
      {data && (
        <div className="op-daily-run">
          <p>报告证据时间：{timeLabel(report?.asOf ?? data.asOf)}</p>
          <p>
            本次计划：{timeLabel(data.scheduledFor)} · 实际开始：
            {timeLabel(data.startedAt)} · 结束：{timeLabel(data.completedAt)}
          </p>
          <small>
            下次计划：{timeLabel(data.nextRunAt)}
            ；计划与执行登记不代表研究服务此刻在线。
          </small>
        </div>
      )}
      {!report ? (
        <p>尚无可展示的每日事件研究；可继续核对下方已有事实与宏观指标。</p>
      ) : (
        <>
          {data?.state !== "READY" && (
            <p role="status">
              当前显示历史报告及原始日期，不代表今日已完成研究。
            </p>
          )}
          <p>{report.summary}</p>
          <p className="op-muted">
            以下影响与情景均为模型条件推演；原文核对不等于投资判断已核实，不生成买卖资格。
          </p>
          {report.events.map((event) => (
            <article className="op-daily-event" key={event.id}>
              <header>
                <h3>{event.title}</h3>
                <span className="op-badge">
                  {phases[event.phase] ?? "阶段待核查"}
                </span>
              </header>
              <p>{event.summary}</p>
              {data?.reportRunId && onTrack && (
                <button
                  type="button"
                  disabled={trackingPending}
                  aria-label={`跟踪${event.title}`}
                  onClick={() => onTrack(data.reportRunId!, event.id)}
                >
                  跟踪此研究
                </button>
              )}
              <small>事件事实尚未独立核验；影响为模型条件推演。</small>
              {refs(event.sourceIds)}
              {!!event.channels.length && (
                <section>
                  <h4>可能传导 · 模型条件推演</h4>
                  {event.channels.map((channel, i) => (
                    <p key={i}>{channel}</p>
                  ))}
                </section>
              )}
              {!!event.scenarios.length && (
                <section>
                  <h4>正反情景与验证条件</h4>
                  {event.scenarios.map((scenario, i) => (
                    <div key={i}>
                      <strong>若：{scenario.condition}</strong>
                      <p>可能影响：{scenario.impact}</p>
                      <p>反方条件：{scenario.counter}</p>
                    </div>
                  ))}
                </section>
              )}
              <p>市场反应：尚未核验；价格同时变化不能直接认定事件因果。</p>
              {!!event.watchFor.length && (
                <section>
                  <h4>接下来核查</h4>
                  {event.watchFor.map((condition) => (
                    <p key={condition}>{condition}</p>
                  ))}
                </section>
              )}
              {!!event.unknowns.length && (
                <section>
                  <h4>尚未确认</h4>
                  {event.unknowns.map((unknown) => (
                    <p key={unknown}>{unknown}</p>
                  ))}
                </section>
              )}
              {!!event.affectedDirectionIds.length && (
                <div className="op-research-actions">
                  {related(event.affectedDirectionIds)}
                </div>
              )}
            </article>
          ))}
          {!!report.upcoming.length && (
            <section aria-label="未来关键事件">
              <h3>未来关键事件</h3>
              {report.upcoming.map((event) => (
                <article key={event.id}>
                  <strong>{event.title}</strong>
                  <p>
                    {timeLabel(event.scheduledAt ?? event.scheduledDate)} ·
                    计划，尚未发生
                  </p>
                  {refs(event.sourceIds)}
                  {event.watchFor.map((condition) => (
                    <p key={condition}>{condition}</p>
                  ))}
                  <div className="op-research-actions">
                    {related(event.affectedDirectionIds)}
                  </div>
                </article>
              ))}
            </section>
          )}
          <EnergyObservations rows={report.energy} />
          {!!report.sources.length && (
            <section aria-label="日报原始来源">
              <h3>原始来源与核对</h3>
              {report.sources.map((source) => (
                <article key={source.id}>
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
                    公布：
                    {timeLabel(source.publishedAt ?? source.publishedDate)} ·
                    获取：{timeLabel(source.retrievedAt)}
                  </small>
                </article>
              ))}
            </section>
          )}
          {!!report.coverage.length && (
            <section aria-label="研究覆盖与未找到主题">
              <h3>研究覆盖</h3>
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
        </>
      )}
    </section>
  );
}

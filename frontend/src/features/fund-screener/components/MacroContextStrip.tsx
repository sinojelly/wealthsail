import { useId, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import type {
  Direction,
  OpportunityContext,
  MacroEvent,
} from "../../../api/investment";
import { sourceHref, timeLabel } from "../macro-display";
import DailyResearchReport, {
  DailyResearchStatus,
} from "./DailyResearchReport";
import MacroEvidencePanel from "./MacroEvidencePanel";
import ActionablePlan from "./ActionablePlan";
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
  selectedIndexId,
  renderStudyTracking,
  onStudyEvent,
  studyPending,
  studyError,
}: {
  data?: OpportunityContext;
  error: boolean;
  loading?: boolean;
  months?: number;
  onMonthsChange?: (months: number) => void;
  directions?: Direction[];
  onDirectionSelect?: (id: string) => void;
  selectedDirection?: Direction;
  selectedIndexId?: string;
  scopeKey?: string;
  renderStudyTracking?: () => ReactNode;
  onStudyEvent?: (
    reportRunId: string,
    eventId: string,
    horizonDays: number,
  ) => void;
  studyPending?: boolean;
  studyError?: string;
}) {
  const [params, setParams] = useSearchParams();
  const panelId = useId();
  const tabs = [
    ["events", "今日重点"],
    ["macro", "市场证据"],
    ["tracking", "我的跟踪与复盘"],
  ] as const;
  const tab =
    params.get("researchTab") === "quality" ||
    params.get("researchData") === "1"
      ? "macro"
      : (tabs.find(([id]) => id === params.get("researchTab"))?.[0] ??
        "events");
  const expanded = params.get("researchOpen") !== "0";
  function open(target = tab, visible = true) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set("researchOpen", visible ? "1" : "0");
      next.set("researchTab", target);
      next.delete("researchData");
      return next;
    });
  }
  const ready = !error && !loading ? data : undefined;
  const reportIds = new Set(
    ready?.dailyResearch?.report?.events.map((event) => event.id) ?? [],
  );
  const portfolio = ready?.portfolio;
  const confirmedBreach =
    portfolio?.state === "READY" &&
    portfolio.heldSelected === true &&
    portfolio.budgetState === "BLOCKED" &&
    portfolio.budgetViolation === true &&
    ready?.decision?.assessment?.riskState === "BREACH";
  return (
    <section
      className="op-panel op-macro-research op-daily-observation"
      aria-label="今日投资观察"
    >
      <header className="op-observation-header">
        <button
          type="button"
          className="op-macro-toggle"
          aria-expanded={expanded}
          aria-controls={panelId}
          onClick={() => open(tab, !expanded)}
        >
          <strong>今日投资观察</strong>
          <span>{expanded ? "收起研究" : "展开研究"}</span>
        </button>
        <p className="op-observation-meta">
          <DailyResearchStatus
            state={ready?.dailyResearch?.state}
            scheduleState={ready?.dailyResearch?.scheduleState}
            hasReport={!!ready?.dailyResearch?.report}
          />
        </p>
        {ready?.dailyResearch?.nextRunAt && (
          <small>下次计划：{timeLabel(ready.dailyResearch.nextRunAt)}</small>
        )}
      </header>
      {confirmedBreach && (
        <p role="alert" className="op-confirmed-risk">
          已确认持仓风险预算超限。市场双轨计划继续独立展示；请先核对个人持仓与风险预算。{" "}
          <a href="/dashboard?scope=MINE">核对我的持仓</a>
        </p>
      )}
      {error && <p role="alert">宏观数据读取失败，请稍后重试。</p>}
      {loading && <p role="status">宏观数据加载中…</p>}
      {expanded && (
        <div id={panelId} className="op-daily-expanded">
          <nav
            role="tablist"
            aria-label="投资观察分类"
            className="op-v2-topic-nav"
          >
            {tabs.map(([id, label], i) => (
              <button
                key={id}
                id={`${panelId}-${id}`}
                role="tab"
                aria-selected={tab === id}
                aria-controls={`${panelId}-content`}
                tabIndex={tab === id ? 0 : -1}
                onClick={() => open(id)}
                onKeyDown={(event) => {
                  const delta =
                    event.key === "ArrowRight"
                      ? 1
                      : event.key === "ArrowLeft"
                        ? -1
                        : 0;
                  if (!delta && event.key !== "Home" && event.key !== "End")
                    return;
                  event.preventDefault();
                  const next =
                    event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? tabs.length - 1
                        : (i + delta + tabs.length) % tabs.length;
                  open(tabs[next][0]);
                  (
                    event.currentTarget.parentElement?.querySelectorAll(
                      "button",
                    )[next] as HTMLButtonElement
                  )?.focus();
                }}
              >
                {label}
              </button>
            ))}
          </nav>
          <div
            role="tabpanel"
            id={`${panelId}-content`}
            aria-labelledby={`${panelId}-${tab}`}
            className="op-research-topic-body"
          >
            {tab === "events" && (
              <>
                <DailyResearchReport
                  data={ready?.dailyResearch}
                  directions={directions}
                  onDirectionSelect={onDirectionSelect}
                  onTrack={onStudyEvent}
                  trackingPending={studyPending}
                  trackingError={studyError}
                />
                {(ready?.actionableZones ?? []).map((result) => (
                  <ActionablePlan
                    key={`${result.subjectKind}:${result.subjectId}`}
                    result={result}
                  />
                ))}
                {!ready?.actionableZones?.length && (
                  <p>
                    双轨价格依据尚未齐备，可在工作台选择标的查看实际研究数据。
                  </p>
                )}
                {!!ready?.events?.filter((event) => !reportIds.has(event.id))
                  .length && (
                  <details>
                    <summary>官方发布与其他观察</summary>
                    <MacroEvents
                      events={ready.events.filter(
                        (event) => !reportIds.has(event.id),
                      )}
                      state={ready.eventState}
                    />
                  </details>
                )}
              </>
            )}
            {tab === "macro" && (
              <>
                <label className="op-macro-window">
                  宏观观察窗口{" "}
                  <select
                    aria-label="宏观观察窗口"
                    value={months}
                    onChange={(event) =>
                      onMonthsChange?.(Number(event.target.value))
                    }
                  >
                    {[12, 36, 60].map((value) => (
                      <option key={value} value={value}>
                        {value} 个月
                      </option>
                    ))}
                  </select>
                </label>
                {ready && (
                  <MacroEvidencePanel
                    data={ready}
                    marketId={selectedDirection?.marketId}
                    directionId={selectedDirection?.id}
                    selectedIndexId={selectedIndexId}
                    months={months}
                  />
                )}
              </>
            )}
            {tab === "tracking" &&
              (renderStudyTracking?.() ?? (
                <p>从今日重点或当前标的保存研究，后续在此核对与复盘。</p>
              ))}
          </div>
        </div>
      )}
    </section>
  );
}

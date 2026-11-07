import SellRiskAlerts from "./SellRiskAlerts";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import type {
  Direction,
  OpportunityContext,
  MacroEvent,
} from "../../../api/investment";
import { sourceHref, timeLabel, monthLabel } from "../macro-display";
import DailyResearchReport, {
  DailyResearchStatus,
} from "./DailyResearchReport";
import MacroEvidencePanel from "./MacroEvidencePanel";
import ActionablePlan from "./ActionablePlan";
import Phase2Evidence from "./Phase2Evidence";
import ValueDirections, { type DirectionTrackInput } from "./ValueDirections";
import QuantitativeScan, { type QuantScanTrackInput } from "./QuantitativeScan";
import { researchSummary } from "../research-reading";
import DeterministicRepairNotice from "./DeterministicRepairNotice";
import ResearchTopicRegistry from "./ResearchTopicRegistry";
import FundObservations from "./FundObservations";
import TopicFundRecommendations from "./TopicFundRecommendations";
import ObservationSection from "./ObservationSection";
import TodayFocus from "./TodayFocus";
import { EconomicEvents, EconomicEventTeasers } from "./EconomicEvents";
import {
  resolveResearchWorkspace,
  workspaceParams,
  type ResearchWorkspace,
} from "../workspace-navigation";
import "../research-header.css";
import "../workspaces.css";

function TopicCatalog(
  props: React.ComponentProps<typeof ResearchTopicRegistry>,
) {
  const [expanded, setExpanded] = useState(false);
  return (
    <details
      id={`topic-catalog-${props.runId}`}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>完整研究主题目录</summary>
      {expanded && <ResearchTopicRegistry {...props} />}
    </details>
  );
}

function MacroOverview({ data }: { data: OpportunityContext }) {
  const [expandedReport, setExpandedReport] = useState<string | null>(null);
  const reportKey =
    data.dailyResearch?.reportRunId ?? data.dailyResearch?.asOf ?? "no-report";
  const expanded = expandedReport === reportKey;
  const summaryId = useId();
  const facts = [
    {
      code: "PMI",
      label: "制造业PMI",
      item: data.items.find((row) => row.code === "PMI"),
    },
    ...[
      ["M1_M2_GAP", "M1−M2剪刀差"],
      ["CN_T10", "中国10年国债"],
      ["US_T10_REAL", "美国10年实际利率"],
    ].map(([code, label]) => ({
      code,
      label,
      item: [...(data.series ?? []), ...(data.alphaPublic?.observations ?? [])]
        .filter((row) => row.code === code)
        .sort((a, b) =>
          String(
            b.asOf ?? ("referenceDate" in b ? b.referenceDate : ""),
          ).localeCompare(
            String(a.asOf ?? ("referenceDate" in a ? a.referenceDate : "")),
          ),
        )[0],
    })),
  ];
  const report = data.dailyResearch?.report;
  const full = researchSummary(
    report?.macroCycle ||
      report?.summary ||
      "综合研究尚未生成，以下为实际已采集观察。",
    Infinity,
  );
  const preview = researchSummary(full, 280);
  return (
    <ObservationSection
      className="op-macro-overview"
      label="今日宏观总纲"
      title="当前宏观事实"
    >
      {report && (
        <h4>
          {data.dailyResearch?.state === "READY"
            ? "宏观周期与今日总纲"
            : "历史宏观研判"}
        </h4>
      )}
      <p id={summaryId}>{expanded ? full : preview}</p>
      {full !== preview && (
        <button
          type="button"
          className="op-macro-expand"
          aria-expanded={expanded}
          aria-controls={summaryId}
          onClick={() => setExpandedReport(expanded ? null : reportKey)}
        >
          {expanded ? "收起今日总纲" : "展开今日总纲"}
        </button>
      )}
      {report && (
        <small>
          研判证据：{timeLabel(report.asOf ?? data.dailyResearch?.asOf)}
        </small>
      )}
      <div className="op-core-facts">
        {facts
          .filter(
            ({ item }) =>
              item?.value != null &&
              !(typeof item.value === "string" && item.value.trim() === "") &&
              Number.isFinite(Number(item.value)) &&
              item.state !== "CONFLICT" &&
              item.state !== "UNAVAILABLE",
          )
          .map(({ code, label, item }) => (
            <div key={code}>
              <span>{label}</span>
              <strong>
                {Number(item!.value).toFixed(code === "PMI" ? 1 : 2)}{" "}
                {code === "M1_M2_GAP" ? "个百分点" : code === "PMI" ? "" : "%"}
              </strong>
              <small>
                {"referencePeriod" in item!
                  ? monthLabel(item.referencePeriod)
                  : timeLabel(
                      "referenceDate" in item!
                        ? item.referenceDate
                        : item!.asOf,
                    )}
              </small>
            </div>
          ))}
      </div>
    </ObservationSection>
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
  refreshing = false,
  refreshError = false,
  months = 36,
  onMonthsChange,
  directions = [],
  onDirectionSelect,
  selectedDirection,
  selectedIndexId,
  renderStudyTracking,
  renderTrackedFunds,
  renderMyFunds,
  renderPersonalReminders,
  renderResearchRefresh,
  onStudyDirection,
  onStudyQuantScan,
}: {
  data?: OpportunityContext;
  error: boolean;
  loading?: boolean;
  refreshing?: boolean;
  refreshError?: boolean;
  months?: number;
  onMonthsChange?: (months: number) => void;
  directions?: Direction[];
  onDirectionSelect?: (id: string) => void;
  selectedDirection?: Direction;
  selectedIndexId?: string;
  scopeKey?: string;
  renderStudyTracking?: () => ReactNode;
  renderTrackedFunds?: () => ReactNode;
  renderMyFunds?: (renderStudyTracking?: () => ReactNode) => ReactNode;
  renderPersonalReminders?: (
    onViewFunds: () => void,
    excludedSellFundCodes: readonly string[],
  ) => ReactNode;
  renderResearchRefresh?: () => ReactNode;
  onStudyDirection?: (input: DirectionTrackInput) => Promise<void>;
  onStudyQuantScan?: (input: QuantScanTrackInput) => Promise<void>;
}) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const revealedReportRoute = useRef<string | null>(null);
  const panelId = useId();
  const tabs = [
    ["events", "今日重点"],
    ["stories", "经济事件"],
    ["tracking", "我的基金"],
    ["macro", "市场证据"],
  ] as const;
  const tab = resolveResearchWorkspace(params);
  const expanded = params.get("researchOpen") !== "0";
  function open(target: ResearchWorkspace = tab, visible = true) {
    setParams((current) => {
      const next = workspaceParams(current, target);
      next.set("researchOpen", visible ? "1" : "0");
      next.set("researchTab", target);
      next.delete("researchData");
      return next;
    });
  }
  function revealResearch(targetId: string) {
    open("macro", true);
    requestAnimationFrame(() => {
      const details = document.getElementById(
        `${panelId}-full-research`,
      ) as HTMLDetailsElement | null;
      const target = document.getElementById(targetId);
      if (details) details.open = true;
      if (target instanceof HTMLDetailsElement) target.open = true;
      target?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    });
  }
  function openEvent(id: string) {
    setParams((current) => {
      const next = workspaceParams(current, "stories");
      next.set("economicEvent", id);
      return next;
    });
  }
  const ready = !error && !loading ? data : undefined;
  const portfolio = ready?.portfolio;
  const daily = ready?.dailyResearch;
  const currentResearchId = daily?.pipeline
    ? daily.pipeline.researchRunId
    : daily?.runId;
  const displayedReportId = daily?.report ? daily.reportRunId : undefined;
  const reportSourceLink = location.hash === "#latest-successful-daily-report";
  const requestedReportId = params.get("reportRunId");
  const reportSourceUnavailable =
    reportSourceLink &&
    !!ready &&
    (!displayedReportId ||
      (!!requestedReportId && requestedReportId !== displayedReportId));
  useEffect(() => {
    if (
      !reportSourceLink ||
      !ready ||
      !expanded ||
      tab !== "macro" ||
      reportSourceUnavailable ||
      revealedReportRoute.current === location.key
    )
      return;
    const details = document.getElementById(`${panelId}-full-research`);
    const target = document.getElementById("latest-successful-daily-report");
    if (!(details instanceof HTMLDetailsElement) || !target) return;
    revealedReportRoute.current = location.key;
    details.open = true;
    target.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [
    reportSourceLink,
    ready,
    expanded,
    tab,
    reportSourceUnavailable,
    location.key,
    panelId,
  ]);
  const isHistoricalReport =
    !!displayedReportId &&
    (displayedReportId !== currentResearchId || daily?.state !== "READY");
  const currentResearchCompletedAt = daily?.pipeline
    ? daily.pipeline.finishedAt
    : daily?.completedAt;
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
        {daily && (
          <p className="op-research-execution-summary">
            本次执行：
            {(
              {
                READY: "已完成",
                RUNNING: "进行中",
                FAILED: "未通过",
                UNCERTAIN: "结果待核实",
                STALE: "本研究日尚未更新",
                NOT_RUN: "尚未启动",
              } as Record<string, string>
            )[daily.pipeline?.state ?? daily.state] ?? "状态待核实"}{" "}
            · 展示内容以报告来源为准
          </p>
        )}
        <details className="op-execution-disclosure">
          <summary>执行与调度详情</summary>
          <p
            className="op-observation-meta"
            role="group"
            aria-label="本次执行状态"
          >
            {daily && (
              <>
                本次执行：
                <DailyResearchStatus data={daily} /> · 执行ID：
                {currentResearchId ?? daily.pipeline?.id ?? "尚未启动"}
              </>
            )}
          </p>
          {ready?.dailyResearch?.nextRunAt && (
            <small className="op-observation-next-run">
              下次计划：{timeLabel(ready.dailyResearch.nextRunAt)}
            </small>
          )}
        </details>
      </header>
      {renderResearchRefresh?.()}
      {ready && (
        <div className="op-research-report-source">
          <p aria-label="展示报告来源" aria-live="polite">
            {daily?.report
              ? `展示报告：${isHistoricalReport ? "最近有效报告 · 历史 · 只读" : "本研究日有效报告 · 只读"} · 完成：${timeLabel(daily.reportCompletedAt)}`
              : "展示报告：尚无有效报告；以下为当前已采集事实与只读观察。"}
          </p>
          {daily?.reportRunId && (
            <div className="op-research-report-links">
              {displayedReportId && (
                <button
                  type="button"
                  onClick={() => revealResearch(`${panelId}-report-plans`)}
                >
                  查看展示报告的买卖计划
                </button>
              )}
              <button
                type="button"
                onClick={() =>
                  revealResearch(`topic-catalog-${displayedReportId}`)
                }
              >
                查看展示报告的主题目录
              </button>
              <details>
                <summary>报告身份</summary>
                <small>报告ID：{daily.reportRunId} · 只读</small>
              </details>
            </div>
          )}
        </div>
      )}
      <DeterministicRepairNotice
        repair={ready?.dailyResearch?.report?.deterministicRepair}
        reportRunId={ready?.dailyResearch?.reportRunId}
      />
      <SellRiskAlerts data={ready?.sellRiskAlerts} />
      {confirmedBreach && (
        <p role="alert" className="op-confirmed-risk">
          已确认持仓风险预算超限。市场双轨计划继续独立展示；请先核对个人持仓与风险预算。{" "}
          <a href="/dashboard?scope=MINE">核对我的持仓</a>
        </p>
      )}
      {error && <p role="alert">展示报告读取失败，请稍后重试。</p>}
      {loading && <p role="status">正在加载展示报告…</p>}
      {reportSourceUnavailable && (
        <p role="status">
          所选报告出处未加载；当前内容不能作为这份报告的出处。
        </p>
      )}
      {refreshing && (
        <p role="status">正在更新展示报告，继续显示上一份已加载内容。</p>
      )}
      {refreshError && (
        <p role="alert">展示报告刷新失败，继续显示上一份已加载内容。</p>
      )}
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
                {ready && (
                  <TodayFocus
                    data={ready}
                    onViewEvidence={() => open("macro")}
                  />
                )}
                {renderPersonalReminders?.(
                  () => open("tracking"),
                  ready?.sellRiskAlerts?.items?.map((item) => item.fundCode) ??
                    [],
                )}
                {ready && (
                  <>
                    {daily?.report && (
                      <TopicFundRecommendations
                        runId={displayedReportId}
                        state="READY"
                        completedAt={daily.reportCompletedAt}
                        source="REPORT"
                        historical={isHistoricalReport}
                        presentation="SUMMARY"
                      />
                    )}
                    {currentResearchId &&
                      currentResearchId !== displayedReportId && (
                        <TopicFundRecommendations
                          runId={currentResearchId}
                          state={daily?.pipeline?.state ?? daily?.state}
                          completedAt={currentResearchCompletedAt}
                          source="CURRENT"
                          presentation="SUMMARY"
                          defaultExpanded={!daily?.report}
                        />
                      )}
                    {!daily?.report && !currentResearchId && (
                      <p role="status">
                        基金机会来源尚未确定，等待有效报告或本次研究记录。
                      </p>
                    )}
                  </>
                )}
                {ready && (
                  <EconomicEventTeasers data={ready} onOpenEvent={openEvent} />
                )}
              </>
            )}
            {tab === "stories" && ready && (
              <EconomicEvents
                data={ready}
                selectedEventId={params.get("economicEvent") ?? undefined}
                onSelectEvent={openEvent}
              />
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
                  <>
                    <MacroOverview data={ready} />
                    <MacroEvidencePanel
                      data={ready}
                      marketId={selectedDirection?.marketId}
                      directionId={selectedDirection?.id}
                      selectedIndexId={selectedIndexId}
                      months={months}
                    />
                    <Phase2Evidence data={ready} />
                    <details
                      id={`${panelId}-full-research`}
                      className="op-print-disclosure"
                    >
                      <summary>展开查看完整研究</summary>
                      {displayedReportId ? (
                        <TopicCatalog
                          key={`report-catalog:${displayedReportId}`}
                          runId={displayedReportId}
                          state="READY"
                          completedAt={daily?.reportCompletedAt}
                          variant="FORMAL"
                        />
                      ) : (
                        <TopicCatalog
                          key={`current-catalog:${currentResearchId}`}
                          runId={currentResearchId}
                          state={daily?.pipeline?.state ?? daily?.state}
                          completedAt={currentResearchCompletedAt}
                          variant="CURRENT"
                        />
                      )}
                      <FundObservations
                        key={`observation:${daily?.fundObservation?.researchId ?? "no-observation"}`}
                        data={daily}
                      />
                      <div
                        id={`${panelId}-report-plans`}
                        role="group"
                        aria-label="展示报告的买卖计划"
                        data-report-run-id={displayedReportId}
                        className="op-report-plan-target"
                      >
                        <ValueDirections
                          key={`formal:${daily?.reportRunId ?? "no-report"}`}
                          data={daily}
                          onTrack={onStudyDirection}
                          onViewMacro={() => open("macro")}
                          presentation="FORMAL"
                        />
                      </div>
                      {ready?.dailyResearch?.report &&
                        ready.dailyResearch.reportRunId && (
                          <section
                            className="op-daily-report"
                            aria-label="最近一次成功研究"
                          >
                            <h3>最近一次成功研究</h3>
                            <p>
                              完成：
                              {timeLabel(ready.dailyResearch.reportCompletedAt)}
                              （北京时间）
                              {ready.dailyResearch.state === "READY"
                                ? " · 本研究日已完成"
                                : " · 今日尚无新的正式研究；此为最近一次成功报告"}
                            </p>
                            <p>
                              {researchSummary(
                                ready.dailyResearch.report.summary || "",
                                380,
                              )}
                            </p>
                            {!!ready.dailyResearch.report.events.length && (
                              <ul
                                className="op-daily-events"
                                aria-label="最近成功研究的主要事件"
                              >
                                {ready.dailyResearch.report.events
                                  .slice(0, 3)
                                  .map((event) => (
                                    <li key={event.id}>
                                      <article className="op-daily-event">
                                        <strong>{event.title}</strong>
                                        <p>
                                          {researchSummary(
                                            event.summary || "",
                                            220,
                                          )}
                                        </p>
                                      </article>
                                    </li>
                                  ))}
                              </ul>
                            )}
                            <a href="#latest-successful-daily-report">
                              查看完整成功报告、新闻、主题与来源
                            </a>
                            <p>
                              <small>
                                历史研究仅供阅读；当前价格、风险及基金操作资格须重新核验。
                              </small>
                            </p>
                          </section>
                        )}
                      <QuantitativeScan
                        data={ready?.dailyResearch}
                        onTrack={onStudyQuantScan}
                      />
                      {!ready?.dailyResearch?.report &&
                        !!ready?.actionableZones?.length && (
                          <section aria-label="程序价格观察">
                            <h3>程序价格观察 · 尚非今日综合研判</h3>
                            {ready.actionableZones.map((result) => (
                              <ActionablePlan
                                key={`${result.subjectKind}:${result.subjectId}`}
                                result={result}
                              />
                            ))}
                          </section>
                        )}
                      <DailyResearchReport
                        data={ready?.dailyResearch}
                        directions={directions}
                        onDirectionSelect={onDirectionSelect}
                        topicRegistryElsewhereRunId={
                          displayedReportId ?? currentResearchId
                        }
                      />
                      {!!ready?.events?.length && (
                        <details
                          className="op-print-disclosure"
                          key={`official:${ready.dailyResearch?.reportRunId ?? ready.dailyResearch?.report?.asOf ?? "no-report"}`}
                        >
                          <summary>官方发布与其他观察</summary>
                          <MacroEvents
                            events={ready.events ?? []}
                            state={ready.eventState}
                          />
                        </details>
                      )}
                    </details>
                  </>
                )}
              </>
            )}
            {tab === "tracking" &&
              (renderMyFunds?.(renderStudyTracking) ??
                renderTrackedFunds?.() ??
                renderStudyTracking?.() ?? (
                  <p>从今日重点或当前标的保存研究，后续在此核对与复盘。</p>
                ))}
          </div>
        </div>
      )}
    </section>
  );
}

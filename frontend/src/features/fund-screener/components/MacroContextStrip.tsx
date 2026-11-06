import SellRiskAlerts from "./SellRiskAlerts";
import { useId, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
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

function TopicCatalog(
  props: React.ComponentProps<typeof ResearchTopicRegistry>,
) {
  const [expanded, setExpanded] = useState(false);
  return (
    <details onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>完整研究主题目录</summary>
      {expanded && <ResearchTopicRegistry {...props} variant="CURRENT" />}
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
    <section className="op-macro-overview" aria-label="今日宏观总纲">
      <h3>
        {report
          ? data.dailyResearch?.state === "READY"
            ? "宏观周期与今日总纲"
            : "历史宏观研判"
          : "当前宏观事实"}
      </h3>
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
    </section>
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
  selectedIndexId,
  renderStudyTracking,
  renderTrackedFunds,
  renderResearchRefresh,
  onStudyDirection,
  onStudyQuantScan,
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
  renderTrackedFunds?: () => ReactNode;
  renderResearchRefresh?: () => ReactNode;
  onStudyDirection?: (input: DirectionTrackInput) => Promise<void>;
  onStudyQuantScan?: (input: QuantScanTrackInput) => Promise<void>;
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
  const daily = ready?.dailyResearch;
  const currentResearchId = daily?.pipeline
    ? daily.pipeline.researchRunId
    : daily?.runId;
  const topicResearchId =
    currentResearchId || (daily?.report ? daily.reportRunId : undefined);
  const retainedTopics =
    !!topicResearchId && topicResearchId !== currentResearchId;
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
          <DailyResearchStatus data={ready?.dailyResearch} />
        </p>
        {ready?.dailyResearch?.nextRunAt && (
          <small className="op-observation-next-run">
            下次计划：{timeLabel(ready.dailyResearch.nextRunAt)}
          </small>
        )}
      </header>
      {renderResearchRefresh?.()}
      {ready?.dailyResearch?.reportRunId && (
        <p>
          <button
            type="button"
            onClick={() => {
              open("events", true);
              requestAnimationFrame(() =>
                document
                  .getElementById(
                    `research-topic-registry-${ready.dailyResearch!.reportRunId}`,
                  )
                  ?.scrollIntoView?.({ behavior: "smooth", block: "start" }),
              );
            }}
          >
            查看展示报告的主题目录
          </button>
          <small> · 报告ID：{ready.dailyResearch.reportRunId} · 只读</small>
        </p>
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
                {retainedTopics && (
                  <p>
                    以下主题来自最近成功报告，保留原研究日期；今日主题尚未更新。
                  </p>
                )}
                <TopicFundRecommendations
                  runId={topicResearchId}
                  state={
                    retainedTopics
                      ? "READY"
                      : (daily?.pipeline?.state ?? daily?.state)
                  }
                  completedAt={
                    retainedTopics
                      ? daily?.reportCompletedAt
                      : daily?.pipeline
                        ? daily.pipeline.finishedAt
                        : daily?.completedAt
                  }
                />
                <TopicCatalog
                  key={`catalog:${topicResearchId}`}
                  runId={topicResearchId}
                  state={
                    retainedTopics
                      ? "READY"
                      : (daily?.pipeline?.state ?? daily?.state)
                  }
                  completedAt={
                    retainedTopics
                      ? daily?.reportCompletedAt
                      : daily?.pipeline
                        ? daily.pipeline.finishedAt
                        : daily?.completedAt
                  }
                />
                <FundObservations
                  key={`observation:${daily?.fundObservation?.researchId ?? "no-observation"}`}
                  data={daily}
                />
                <ValueDirections
                  key={`formal:${daily?.reportRunId ?? "no-report"}`}
                  data={daily}
                  onTrack={onStudyDirection}
                  onViewMacro={() => open("macro")}
                  presentation="FORMAL"
                />
                {ready?.dailyResearch?.report &&
                  ready.dailyResearch.reportRunId && (
                    <section
                      className="op-daily-report"
                      aria-label="最近一次成功研究"
                    >
                      <h3>最近一次成功研究</h3>
                      <p>
                        完成：{timeLabel(ready.dailyResearch.reportCompletedAt)}
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
                                    {researchSummary(event.summary || "", 220)}
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
                {ready && <MacroOverview data={ready} />}
                {renderTrackedFunds?.()}
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
                  topicRegistryElsewhereRunId={topicResearchId}
                />
                {!!ready?.events?.filter((event) => !reportIds.has(event.id))
                  .length && (
                  <details
                    className="op-print-disclosure"
                    key={`official:${ready.dailyResearch?.reportRunId ?? ready.dailyResearch?.report?.asOf ?? "no-report"}`}
                  >
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
                  <>
                    <MacroEvidencePanel
                      data={ready}
                      marketId={selectedDirection?.marketId}
                      directionId={selectedDirection?.id}
                      selectedIndexId={selectedIndexId}
                      months={months}
                    />
                    <Phase2Evidence data={ready} />
                  </>
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

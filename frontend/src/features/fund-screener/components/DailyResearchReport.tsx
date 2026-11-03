import { useRef, useState, type ReactNode } from "react";
import type {
  DailyResearch,
  DailyResearchFailureReasonCode,
  DailyResearchFailureRuleId,
  DailyResearchFailureStage,
  Direction,
} from "../../../api/investment";
import { sourceHref, timeLabel } from "../macro-display";
import { readingText, sourceTitle } from "../research-reading";
import { researchScheduleLabel } from "../research-schedule-display";
import ResearchAcceptance from "./ResearchAcceptance";
import DeterministicRepairNotice from "./DeterministicRepairNotice";
import { PublishedResearchRepairs } from "../../operations/components/ResearchHistoryRepairs";
import ResearchTopicRegistry from "./ResearchTopicRegistry";
const stateLabels: Record<string, string> = {
  NOT_RUN: "今日尚未启动",
  RUNNING: "研究正在执行",
  READY: "研究成功 · 报告已生成",
  STALE: "今日未完成 · 显示历史报告",
  FAILED: "研究失败",
  UNCERTAIN: "执行异常 · 待核实",
  UNKNOWN: "执行状态未知",
  DISABLED: "自动调度已关闭",
  MISSED: "本次研究时段已结束",
};
const stageLabels: Record<string, string> = {
  COLLECTING: "正在采集公共数据",
  WAITING_SOURCES: "等待原公共数据采集任务完成",
  VALIDATING_INPUT: "正在检查研究输入",
  MODEL_RUNNING: "AI研究中",
  VERIFYING_OUTPUT: "正在核验报告",
  PERSISTING: "正在保存报告",
};
const stages = [
  ["COLLECTING", "采集公共数据"],
  ["VALIDATING_INPUT", "检查输入"],
  ["MODEL_RUNNING", "AI研究"],
  ["VERIFYING_OUTPUT", "核验报告"],
  ["PERSISTING", "保存报告"],
] as const;
const failureStages: Record<DailyResearchFailureStage, string> = {
  MODEL_EXECUTION: "模型执行",
  SOURCE_VERIFICATION: "来源核验",
  REPORT_VALIDATION: "报告业务校验",
  PERSISTING: "保存报告",
};
const failureReasons: Record<DailyResearchFailureReasonCode, string> = {
  DIRECTION_VALIDATION_FAILED: "价值方向未通过校验",
  FUND_PLAN_INVALID: "基金计划不符合要求",
  SCHEDULE_DATE_INVALID: "计划事件日期无效",
  SCHEMA_VALIDATION_FAILED: "报告结构不符合要求",
  SOURCE_DATE_INVALID: "来源发布日期无效",
  SOURCE_REFERENCE_INVALID: "来源引用无效",
  TIMEOUT: "执行超时",
  NETWORK_UNAVAILABLE: "网络暂不可用",
  PAYLOAD_VALIDATION_FAILED: "报告内容未通过校验",
  UNEXPECTED_ERROR: "执行发生未分类异常",
  CANCELLED: "执行已取消",
  PIPELINE_RUNTIME_EXPIRED: "研究流程超过运行时限",
};
const failureRules: Record<DailyResearchFailureRuleId, string> = {
  INDUSTRY_REVIEW_DUPLICATE: "行业反方核查记录重复",
  INDUSTRY_REVIEW_COVERAGE_MISSING: "候选行业反方核查覆盖不完整",
  INDUSTRY_RISK_CHECK_DUPLICATE: "行业风险核查项重复",
  INDUSTRY_RISK_CHECK_MISSING: "行业风险核查项缺失",
  INDUSTRY_RISK_SOURCE_MISSING: "行业风险证据缺少来源",
  INDUSTRY_CYCLE_SOURCE_INVALID: "产业周期证据引用的来源无效",
  DIRECTION_INDUSTRY_REVIEW_UNKNOWN: "价值方向引用了未知行业核查记录",
  DIRECTION_DECLARED_INDUSTRY_UNKNOWN: "价值方向声明了未知行业",
  DIRECTION_PLAN_SECTOR_UNREVIEWED: "基金计划引用的行业尚未完成反方核查",
  INDUSTRY_REVIEW_BUDGET_EXCEEDED: "候选行业数量超过核查预算",
  DIRECTION_MACRO_REFERENCE_UNKNOWN: "价值方向引用了未知宏观指标",
  DIRECTION_CALIBRATION_RULE_UNKNOWN: "价值方向引用了未知校准规则",
  DIRECTION_EVIDENCE_INSUFFICIENT: "价值方向的证据不足",
};
const validationReasons = new Set<string>([
  "DIRECTION_VALIDATION_FAILED",
  "FUND_PLAN_INVALID",
  "SCHEDULE_DATE_INVALID",
  "SCHEMA_VALIDATION_FAILED",
  "SOURCE_DATE_INVALID",
  "SOURCE_REFERENCE_INVALID",
  "PAYLOAD_VALIDATION_FAILED",
]);
// Match backend/macro/industry_contract.py; model-provided strings are not codes.
const standardIndustryCodes = new Set<string>([
  "ACTIVE_GROWTH",
  "AEROSPACE",
  "AI",
  "BANK",
  "BROAD",
  "BROKER",
  "CHEMICAL",
  "COAL",
  "COMMUNICATION",
  "CONSUMPTION",
  "DEFENCE",
  "DIVIDEND",
  "DRUG",
  "FINANCE_POLICY",
  "FINANCIAL",
  "FINTECH",
  "GAMING",
  "GLOBAL_ENERGY",
  "GOLD",
  "GOLD_ENERGY",
  "HEALTHCARE_CLINICAL",
  "HK_TECH",
  "INNOVATIVE_DRUG",
  "LOW_ALTITUDE",
  "MEMORY",
  "NONFERROUS",
  "PCB",
  "POWER",
  "RARE_EARTH",
  "RARE_EARTH_METALS",
  "ROBOT",
  "ROBOTICS_MOBILITY",
  "SATELLITE",
  "SEMICONDUCTOR_AI",
  "SOLID_BATTERY",
  "TRADITIONAL_MEDICINE",
  "US_TECH",
]);
function diagnosticLabel(
  labels: Readonly<Record<string, string>>,
  value: unknown,
) {
  return typeof value === "string" && Object.hasOwn(labels, value)
    ? labels[value]
    : undefined;
}
type FailureDetails = Pick<
  DailyResearch,
  | "failureStage"
  | "failureReasonCode"
  | "failureRuleId"
  | "failureItemIndex"
  | "failureIndustryCodes"
  | "runId"
> & {
  pipeline?: Pick<
    NonNullable<DailyResearch["pipeline"]>,
    "researchRunId"
  > | null;
};
export function DailyResearchFailureDiagnosis({
  data,
}: {
  data: FailureDetails;
}) {
  const stage = diagnosticLabel(failureStages, data.failureStage);
  const reason = diagnosticLabel(failureReasons, data.failureReasonCode);
  const rule = diagnosticLabel(failureRules, data.failureRuleId);
  const validation =
    data.failureStage === "REPORT_VALIDATION" &&
    (!!rule || (reason && validationReasons.has(data.failureReasonCode!)));
  const runId = data.pipeline ? data.pipeline.researchRunId : data.runId;
  const safeRunId =
    typeof runId === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      runId,
    )
      ? runId
      : "身份未记录";
  const index = data.failureItemIndex;
  const industryCodes = Array.isArray(data.failureIndustryCodes)
    ? [
        ...new Set(
          data.failureIndustryCodes.filter(
            (code: unknown): code is string =>
              typeof code === "string" && standardIndustryCodes.has(code),
          ),
        ),
      ]
        .sort()
        .slice(0, 10)
    : [];
  return (
    <div role="group" aria-label="研究失败诊断">
      {validation && (
        <p>
          <strong>报告未通过业务校验</strong>
        </p>
      )}
      <p>失败原因：{rule ?? reason ?? "原因待核查"}</p>
      <p>失败阶段：{stage ?? "阶段待核查"}</p>
      {reason && <p>原因代码：{data.failureReasonCode}</p>}
      {rule && <p>校验规则：{data.failureRuleId}</p>}
      {!!industryCodes.length && (
        <p>相关行业代码：{industryCodes.join("、")}</p>
      )}
      {typeof index === "number" &&
        Number.isSafeInteger(index) &&
        index >= 0 &&
        index <= 9999 && <p>失败条目：数组索引 {index}（从0开始）</p>}
      <p>研究执行ID：{safeRunId}</p>
    </div>
  );
}
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
function executionState(data?: DailyResearch | null, state?: string) {
  return data?.pipeline?.state ?? data?.state ?? state;
}
function triggerTime(data?: DailyResearch | null) {
  const actual = data?.pipeline?.startedAt ?? data?.startedAt;
  if (actual && timeLabel(actual) !== "未确认")
    return `（触发：${timeLabel(actual)} 北京时间）`;
  if (data?.state === "DISABLED" || data?.scheduleState === "DISABLED")
    return "";
  if (data?.scheduleState === "NON_WORKDAY") {
    return data.nextRunAt && timeLabel(data.nextRunAt) !== "未确认"
      ? `（下次计划触发：${timeLabel(data.nextRunAt)} 北京时间）`
      : "";
  }
  const planned = data?.todayScheduledFor;
  return planned && timeLabel(planned) !== "未确认"
    ? `（计划触发：${timeLabel(planned)} 北京时间）`
    : "";
}
function statusLabel(
  state?: string,
  scheduleState?: string,
  hasReport?: boolean,
  stage?: string,
  runMode?: DailyResearch["runMode"],
) {
  if (
    state === "DISABLED" ||
    (scheduleState === "DISABLED" && runMode !== "MANUAL")
  )
    return stateLabels.DISABLED;
  if (!state || state === "NOT_RUN" || state === "STALE") {
    if (scheduleState === "NON_WORKDAY") return "非工作日，不安排自动研究";
    if (scheduleState === "MISSED") return stateLabels.MISSED;
    if (scheduleState === "DUE_NOT_DISPATCHED")
      return "已到计划时间 · 尚未启动";
  }
  if (state === "RUNNING")
    return stageLabels[stage ?? ""] ?? stateLabels.RUNNING;
  if (state === "STALE" && hasReport === false) return "暂无有效日报";
  if (state === "READY" && hasReport === false) return "研究完成，报告暂不可用";
  return stateLabels[state ?? ""] ?? "执行状态未知";
}
export function DailyResearchStatus({
  data,
  state,
  scheduleState,
  hasReport,
}: {
  data?: DailyResearch | null;
  state?: string;
  scheduleState?: string;
  hasReport?: boolean;
}) {
  const currentState = executionState(data, state);
  const label = statusLabel(
    currentState,
    data?.scheduleState ?? scheduleState,
    data ? !!data.report : hasReport,
    data?.pipeline?.stage,
    data?.runMode,
  );
  return (
    <span className="op-research-status" data-state={currentState}>
      {label}
      {triggerTime(data)}
    </span>
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
      key={props.data?.reportRunId ?? props.data?.report?.asOf ?? "no-report"}
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
  const eventSourceIds = new Set(events.flatMap((event) => event.sourceIds));
  const remainingSourceIds =
    report?.sources
      .filter((source) => !eventSourceIds.has(source.id))
      .map((source) => source.id) ?? [];
  const importance: Record<string, number> = { HIGH: 0, MEDIUM: 1, LOW: 2 };
  const priorityEvents = events
    .map((event, order) => ({ event, order }))
    .sort(
      (a, b) =>
        (importance[a.event.importance] ?? 3) -
          (importance[b.event.importance] ?? 3) || a.order - b.order,
    )
    .slice(0, 3)
    .map(({ event }) => event);
  const upcoming =
    report?.upcoming.filter(
      (event, index, rows) =>
        rows.findIndex((row) => row.id === event.id) === index,
    ) ?? [];
  const errorClass = data?.pipeline?.errorClass ?? data?.errorClass;
  const safeErrorClass =
    errorClass && /^[A-Za-z][A-Za-z0-9_.]{0,79}$/.test(errorClass)
      ? errorClass
      : "未分类错误";
  const currentState = executionState(data);
  const currentStage =
    data?.pipeline?.stage === "WAITING_SOURCES"
      ? "COLLECTING"
      : data?.pipeline?.stage;
  const currentStageIndex = stages.findIndex(
    ([stage]) => stage === currentStage,
  );
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
                  {sourceTitle(source.title)}
                </a>
              ) : (
                <strong>{sourceTitle(source.title)}</strong>
              )}
              <p>
                <span className="op-badge">
                  {source.verification === "SOURCE_CHECKED"
                    ? "原文已核对"
                    : source.verification === "UNVERIFIED" ||
                        source.verification === "REPORTED"
                      ? "媒体快讯"
                      : "来源状态待补充"}
                </span>{" "}
                {source.verification === "SOURCE_CHECKED"
                  ? "引用与原始发布匹配；影响为研究推演。"
                  : source.verification === "UNVERIFIED" ||
                      source.verification === "REPORTED"
                    ? "保留报道中的公告与事件阶段，用于产业催化研究。"
                    : "来源信息尚未齐全。"}
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
            if (direction)
              return `${direction.marketLabel} · ${direction.label}`;
            const valueDirection = report?.valueDirections?.find(
              (row) => row.key === id,
            );
            return valueDirection
              ? readingText(valueDirection.title, 100)
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
      {(data?.reportRunMode === "DEBUG" ||
        data?.reportRunMode === "MANUAL") && (
        <p className="op-debug-version">
          {data.reportRunMode === "MANUAL"
            ? "当前报告：手动研究版（显式重新采集与分析）"
            : "当前报告：调试版（显式重新采集与分析）"}
        </p>
      )}
      {report && <p>报告证据时间：{timeLabel(report.asOf ?? data?.asOf)}</p>}
      <DeterministicRepairNotice
        repair={report?.deterministicRepair}
        reportRunId={data?.reportRunId}
      />
      <ResearchAcceptance
        runId={data?.pipeline ? data.pipeline.researchRunId : data?.runId}
        state={currentState}
        completedAt={
          data?.pipeline ? data.pipeline.finishedAt : data?.completedAt
        }
      />
      <PublishedResearchRepairs />
      {report && data?.reportRunId && (
        <ResearchTopicRegistry
          runId={data.reportRunId}
          state="READY"
          completedAt={data.reportCompletedAt}
          variant="FORMAL"
        />
      )}
      {(data?.pipeline ? data.pipeline.researchRunId : data?.runId) !==
        data?.reportRunId && (
        <ResearchTopicRegistry
          runId={data?.pipeline ? data.pipeline.researchRunId : data?.runId}
          state={currentState}
          completedAt={
            data?.pipeline ? data.pipeline.finishedAt : data?.completedAt
          }
          variant="CURRENT"
        />
      )}
      {!report ? (
        <>
          <p>有效报告尚未生成，可先核查市场证据。</p>
          {children}
        </>
      ) : (
        <>
          {currentState !== "READY" && (
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
            className="op-daily-full-report op-print-disclosure"
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
            {!!remainingSourceIds.length && sources(remainingSourceIds)}
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
            <DailyResearchStatus data={data} />
          </p>
          <p>本次任务：{data.pipeline?.id ?? data.runId ?? "尚未启动"}</p>
          <p>
            计划触发：{timeLabel(data.todayScheduledFor ?? data.scheduledFor)} ·
            实际开始：
            {timeLabel(data.pipeline?.startedAt ?? data.startedAt)} · 结束：
            {timeLabel(data.pipeline?.finishedAt ?? data.completedAt)} 北京时间
          </p>
          {data.pipeline && (
            <>
              {data.pipeline.startWindowEndsAt && (
                <p>
                  本次启动/恢复截止：
                  {timeLabel(data.pipeline.startWindowEndsAt)} 北京时间
                </p>
              )}
              {data.pipeline.runtimeDeadlineAt && (
                <p>
                  本次运行截止：{timeLabel(data.pipeline.runtimeDeadlineAt)}{" "}
                  北京时间
                </p>
              )}
              <ol className="op-research-stages" aria-label="研究执行阶段">
                {stages.map(([stage, label], index) => (
                  <li
                    key={stage}
                    data-progress={
                      currentState === "READY" || index < currentStageIndex
                        ? "complete"
                        : index === currentStageIndex
                          ? "current"
                          : "pending"
                    }
                  >
                    <span
                      aria-current={
                        stage === currentStage && currentState !== "READY"
                          ? "step"
                          : undefined
                      }
                    >
                      {stage === "COLLECTING" &&
                      data.pipeline?.stage === "WAITING_SOURCES"
                        ? stageLabels.WAITING_SOURCES
                        : label}
                    </span>
                  </li>
                ))}
              </ol>
              {data.pipeline.updatedAt && (
                <p>最近进展：{timeLabel(data.pipeline.updatedAt)} 北京时间</p>
              )}
              {data.pipeline.quality && (
                <p>
                  输入检查：
                  {data.pipeline.quality.allowed
                    ? "关键数据已满足研究要求"
                    : "关键数据不足，未调用AI"}
                  {data.pipeline.quality.status === "PARTIAL" &&
                  data.pipeline.quality.allowed
                    ? "（部分来源暂缺）"
                    : ""}
                </p>
              )}
              {data.pipeline.sourceBatchId && (
                <p>数据批次：{data.pipeline.sourceBatchId}</p>
              )}
              {data.pipeline.researchRunId && (
                <p>AI执行记录：{data.pipeline.researchRunId}</p>
              )}
            </>
          )}
          {(currentState === "FAILED" || currentState === "UNCERTAIN") && (
            <>
              <DailyResearchFailureDiagnosis data={data} />
              <p>
                安全错误类：<span>{safeErrorClass}</span>
              </p>
            </>
          )}
          {report && (
            <p>
              当前展示报告：{data.reportRunId ?? "身份未记录"} · 报告完成：
              {timeLabel(data.reportCompletedAt)} · 研究证据：
              {timeLabel(report.asOf ?? data.asOf)}
            </p>
          )}
          <small>
            {data.scheduleState === "DISABLED"
              ? "自动调度已关闭"
              : researchScheduleLabel(data.schedule)}
            ，完成时间取决于采集与研究耗时。
            {data.scheduleState !== "DISABLED" && data.nextRunAt
              ? ` 下次计划：${timeLabel(data.nextRunAt)} 北京时间。`
              : ""}
            计划时间不代表研究服务此刻在线。
          </small>
        </details>
      )}
    </section>
  );
}

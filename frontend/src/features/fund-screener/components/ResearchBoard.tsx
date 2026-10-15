import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type {
  OpportunityContext,
  ResearchAction,
  ResearchIssue,
  ResearchTone,
} from "../../../api/investment";
import { timeLabel } from "../macro-display";

const labels: Record<string, string> = {
  UNKNOWN: "尚未评估",
  INSUFFICIENT: "证据不足",
  INCOMPLETE: "证据不完整",
  READY: "条件齐备",
  BLOCKED: "条件未满足",
  PARTIAL: "部分覆盖",
  PRIORITY_RESEARCH: "值得优先研究",
  WAIT_CONDITIONS: "等待条件",
  RISK_OBSERVATION: "风险观察",
  POSITION_REVIEW: "持仓风险复核",
  WAIT: "等待完整条件",
  ENTRY_CANDIDATE: "建仓研究候选",
  HOLD_REVIEW: "持仓观察",
  VERIFIED: "证据已核验",
  NOT_COLLECTED: "尚未采集",
  PERMISSION_REQUIRED: "需要来源授权",
  STALE: "旧期证据",
  CONFLICT: "证据冲突",
  UNAVAILABLE: "证据不可用",
  SUCCESS: "采集完成",
  COMPLETED: "处理完成",
  FAILED: "采集失败",
  CANCELLED: "处理已取消",
  CLAIMED: "已登记执行租约",
  SKIPPED: "本次跳过",
  BREACH: "已确认预算超限",
  CHECKED: "已完成预算核查",
  OBSERVED: "事实观察",
  NO_CONFIRMED_CHANGE: "暂无已确认变化",
  HELD: "已持有",
  NOT_HELD: "未持有",
  UNVERIFIED: "待核验",
  FINISHED: "最近处理已结束",
};
const stateLabel = (state: string) => labels[state] ?? "待核查";
const categoryLabels: Record<ResearchIssue["category"], string> = {
  RISK: "风险复核",
  OPPORTUNITY: "研究线索",
  CHANGE: "重要变化",
  DATA_GAP: "研究受限",
  SYSTEM: "数据状态",
};
const ownerLabels: Record<string, string> = {
  MACRO_WORKER: "宏观采集任务",
  MARKET_WORKER: "市场采集任务",
  WORKER: "后台采集任务",
  RESEARCH: "研究服务",
  SYSTEM: "系统",
  ADMIN: "管理员",
  USER: "用户",
  DATA_GOVERNANCE: "数据核验服务",
  PORTFOLIO: "资产服务",
  INVESTMENT_RESEARCH: "研究服务",
  RESEARCH_SERVICE: "研究服务",
  RESEARCH_DATA: "研究数据服务",
};
const sourceLabels: Record<string, string> = {
  PBOC: "中国人民银行",
  US_TREASURY: "美国财政部",
  TREASURY_NOMINAL: "美国财政部名义利率",
  TREASURY_REAL: "美国财政部实际利率",
  NBS: "国家统计局",
  PRE_MARKET: "已存盘前事实",
  CHINABOND: "中债来源",
  CFETS: "外汇交易中心来源",
  UST_NOMINAL: "美国财政部名义利率",
  UST_REAL: "美国财政部实际利率",
  PBOC_M1: "中国人民银行货币供应",
  PBOC_TSF: "中国人民银行社会融资",
  PBOC_LOANS: "中国人民银行贷款数据",
};
const dataIssue = (issue: ResearchIssue) =>
  ["DATA_GAP", "SYSTEM"].includes(issue.category);
const evidenceLabels: Record<string, string> = {
  INDEX_TREND: "当前指数趋势",
  INDEX_VALUATION: "当前指数估值",
  INDEX_EARNINGS: "企业盈利证据",
  portfolio: "个人组合证据",
  "confirmed-policy": "已确认风险政策",
  PMI: "制造业PMI",
  CN_T10: "中国10年期国债收益率",
  DR007: "银行间资金价格",
  M1_YOY: "M1同比",
  TSF_STOCK_YOY: "社融存量同比",
  US_T10_NOMINAL: "美国10年期名义国债收益率",
  US_T10_REAL: "美国10年期实际利率",
  CORP_MEDLONG_LOANS_YTD: "企业中长期贷款累计新增",
  USD_FX: "美元及汇率事实",
  YIELD_CURVE_DURATION: "收益率曲线及久期",
  CREDIT_SPREAD_DEFAULT: "信用利差及违约事实",
  FUND_MAPPING: "基金归属",
  LOCAL_ASSET_EVIDENCE: "当地资产基本面与风险",
};
function confirmedRisk(issue: ResearchIssue, data: OpportunityContext) {
  return (
    issue.category === "RISK" &&
    issue.severity === "HIGH" &&
    issue.confidence === "VERIFIED" &&
    issue.holdingRelevance === "HELD" &&
    data.decision?.state === "POSITION_REVIEW" &&
    data.portfolio?.state === "READY" &&
    data.portfolio.heldSelected === true &&
    data.portfolio.budgetViolation === true &&
    data.portfolio.budgetState === "BLOCKED"
  );
}
function issueTone(
  issue: ResearchIssue,
  data: OpportunityContext,
): ResearchTone {
  if (dataIssue(issue)) return "GRAY";
  if (issue.tone === "RED")
    return confirmedRisk(issue, data) ? "RED" : "ORANGE";
  if (
    issue.tone === "GREEN" &&
    (issue.category !== "OPPORTUNITY" || issue.confidence !== "VERIFIED")
  )
    return "GRAY";
  return issue.tone;
}
const toneClass = (tone: ResearchTone) =>
  `op-tone-${tone.toLowerCase()}${tone === "RED" ? " op-critical-impact" : ""}`;
const localRoutes: Partial<Record<ResearchAction["type"], string>> = {
  VIEW_HOLDINGS: "/dashboard?scope=MINE",
  VIEW_POLICY: "/risk-policy",
  UPDATE_HOLDINGS: "/onboarding",
  VIEW_OPERATIONS: "/operations",
};
const topicTargets = [
  "research-macro",
  "research-market",
  "research-global",
  "research-strategy",
];

export interface EvidenceTopic {
  id: string;
  label: string;
  render: () => ReactNode;
}

export default function ResearchBoard({
  data,
  topics,
  canViewOperations = false,
  renderTracking,
  onTrack,
  trackingEnabled = false,
  directionLabel,
}: {
  data: OpportunityContext;
  topics: EvidenceTopic[];
  canViewOperations?: boolean;
  renderTracking: () => ReactNode;
  onTrack?: () => void;
  trackingEnabled?: boolean;
  directionLabel?: string;
}) {
  const board = data.researchBoard!;
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [openedTopics, setOpenedTopics] = useState<string[]>([]);
  const [openedSummaries, setOpenedSummaries] = useState<string[]>([]);
  const [referencedItems, setReferencedItems] = useState<string[]>([]);
  const [dataOpen, setDataOpen] = useState(false);
  const [trackingOpen, setTrackingOpen] = useState(false);
  const items = [
    ...new Map(board.items.map((item) => [item.id, item])).values(),
  ];
  const visibleIds = new Set([
    ...referencedItems,
    ...board.summaries
      .filter((s) => openedSummaries.includes(s.id))
      .flatMap((s) => s.itemIds),
  ]);
  const visibleIssues = items.filter(
    (item) => !dataIssue(item) && visibleIds.has(item.id),
  );
  const gapIssues = items.filter(dataIssue);
  function showReferences(ids: string[]) {
    setReferencedItems((previous) => [...new Set([...previous, ...ids])]);
    if (items.some((item) => ids.includes(item.id) && dataIssue(item)))
      setDataOpen(true);
  }
  function perform(action: ResearchAction) {
    if (action.type === "VIEW_DATA") setDataOpen(true);
    if (action.type === "VIEW_EVIDENCE") {
      setEvidenceOpen(true);
      if (action.target === "research-data") setDataOpen(true);
      else if (action.target && topicTargets.includes(action.target)) {
        setOpenedTopics((previous) => [
          ...new Set([...previous, action.target!]),
        ]);
      }
    }
    if (action.type === "VIEW_CANDIDATES") {
      const target = document.getElementById("op-research-targets");
      target?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      target?.focus({ preventScroll: true });
    }
    if (action.type === "TRACK") {
      setTrackingOpen(true);
      onTrack?.();
    }
  }
  function renderAction(action: ResearchAction) {
    if (action.type === "VIEW_OPERATIONS" && !canViewOperations)
      return <small>仅管理员可查看运行情况。</small>;
    const route = localRoutes[action.type];
    if (action.enabled && route && action.href === route)
      return <Link to={route}>{action.label}</Link>;
    const validLocal = [
      "VIEW_CANDIDATES",
      "VIEW_EVIDENCE",
      "VIEW_DATA",
      "TRACK",
    ].includes(action.type);
    const disabled =
      !action.enabled || !validLocal || (action.type === "TRACK" && !onTrack);
    return (
      <>
        <button
          type="button"
          disabled={disabled}
          onClick={() => perform(action)}
        >
          {action.label}
        </button>
        {disabled && (
          <small>
            {action.type === "TRACK" && trackingEnabled
              ? "当前研究范围已启用跟踪。"
              : (action.disabledReason ?? "当前操作入口尚未接入。")}
          </small>
        )}
      </>
    );
  }
  function renderIssue(issue: ResearchIssue) {
    const tone = issueTone(issue, data);
    return (
      <article
        key={issue.id}
        id={`research-issue-${issue.id}`}
        className={`op-research-issue ${toneClass(tone)}`}
      >
        <div className="op-research-issue-heading">
          <h3>{issue.summary}</h3>
          <span className="op-badge">
            {categoryLabels[issue.category]} ·{" "}
            {
              {
                VERIFIED: "证据已核验",
                PARTIAL: "部分证据",
                UNKNOWN: "证据未确认",
              }[issue.confidence]
            }
          </span>
        </div>
        <p className={toneClass(tone)}>{issue.impact}</p>
        {issue.blocker && (
          <div className="op-research-blocker">
            {issue.blocker.reason !== issue.impact && (
              <p>{issue.blocker.reason}</p>
            )}
            <p>
              影响判断：{issue.blocker.affectedJudgment} ·{" "}
              {stateLabel(issue.blocker.state)}
            </p>
            <p>
              处理责任：{ownerLabels[issue.blocker.owner] ?? "相关数据服务"} ·{" "}
              {issue.blocker.userFixable ? "可按操作补充" : "由负责模块处理"}
            </p>
            <p>
              最近处理：
              {issue.blocker.lastAttemptAt
                ? timeLabel(issue.blocker.lastAttemptAt)
                : "尚无处理记录"}{" "}
              · 下次处理时间：
              {issue.blocker.nextCheckAt
                ? timeLabel(issue.blocker.nextCheckAt)
                : "待确认"}
            </p>
          </div>
        )}
        {!!issue.evidenceIds.length && (
          <small>
            关联证据：
            {issue.evidenceIds
              .map(
                (id) =>
                  data.items.find((i) => i.code === id)?.label ??
                  data.series?.find((s) => s.code === id)?.label ??
                  data.marketEvidence?.find((index) => index.indexId === id)
                    ?.label ??
                  data.events?.find((event) => event.id === id)?.title ??
                  evidenceLabels[id] ??
                  "其他关联证据",
              )
              .join("、")}
          </small>
        )}
        {issue.lastChangedAt && (
          <small>条件变化：{timeLabel(issue.lastChangedAt)}</small>
        )}
        <div className="op-research-actions">
          {renderAction(issue.nextAction)}
        </div>
      </article>
    );
  }
  return (
    <section
      className="op-panel op-research-board"
      aria-label="市场环境与研究重点"
    >
      <header>
        <h2>市场环境与研究重点</h2>
        <span className="op-muted">
          当前研究范围 · {board.asOf ? timeLabel(board.asOf) : "研究时点待确认"}
        </span>
      </header>
      <div className="op-research-summaries">
        {board.summaries.map((summary) => {
          const related = items.filter(
            (item) => summary.itemIds.includes(item.id) && !dataIssue(item),
          );
          const tone =
            summary.tone === "RED"
              ? related.some((item) => confirmedRisk(item, data))
                ? "RED"
                : "GRAY"
              : summary.tone;
          return (
            <section
              key={summary.id}
              aria-label={summary.label}
              className={`op-research-summary ${toneClass(tone)}`}
            >
              <h3>{summary.label}</h3>
              <span className="op-badge">{stateLabel(summary.state)}</span>
              <p className={toneClass(tone)}>{summary.summary}</p>
              <div className="op-research-actions">
                {renderAction(summary.nextAction)}
                {!!related.length && (
                  <button
                    type="button"
                    aria-label={`查看${summary.label}事项`}
                    aria-expanded={openedSummaries.includes(summary.id)}
                    onClick={() =>
                      setOpenedSummaries((previous) =>
                        previous.includes(summary.id)
                          ? previous.filter((id) => id !== summary.id)
                          : [...previous, summary.id],
                      )
                    }
                  >
                    查看事项（{related.length}）
                  </button>
                )}
              </div>
            </section>
          );
        })}
      </div>
      {!!gapIssues.length && (
        <div className="op-research-limitation">
          <span>部分研究判断受数据覆盖限制。</span>
          <button type="button" onClick={() => setDataOpen(true)}>
            查看研究事项与缺口
          </button>
        </div>
      )}
      {!!visibleIssues.length && (
        <section className="op-research-issues" aria-label="研究事项详情">
          {visibleIssues.map(renderIssue)}
        </section>
      )}
      <section className="op-research-direction" aria-label="当前研究方向">
        <h3>{directionLabel ?? board.direction.label}</h3>
        <p>
          市场研究：{stateLabel(board.direction.researchState)} · 研究资格：
          {stateLabel(board.direction.qualificationState)}
        </p>
        {board.direction.supports.map((e, index) => (
          <p key={`support-${index}`}>
            支持依据：{e.summary} · {stateLabel(e.state)}
          </p>
        ))}
        {board.direction.counterFactors.map((e, index) => (
          <p key={`counter-${index}`}>
            反方条件：{e.summary} · {stateLabel(e.state)}
          </p>
        ))}
        {!!board.direction.recommendedChecks.length && (
          <details>
            <summary>进一步研究建议</summary>
            {board.direction.recommendedChecks.map((check) => (
              <p key={check}>{check}</p>
            ))}
          </details>
        )}
        {!!board.direction.itemIds.length && (
          <button
            type="button"
            onClick={() => showReferences(board.direction.itemIds)}
          >
            查看方向关联事项
          </button>
        )}
        <small>
          有限研究线索独立于建仓资格；估值、基金质量、历史验证与个人条件分别核验。
        </small>
      </section>
      <section className="op-research-evidence" aria-label="研究依据">
        <button
          type="button"
          className="op-research-toggle"
          aria-expanded={evidenceOpen}
          onClick={() => setEvidenceOpen(!evidenceOpen)}
        >
          {evidenceOpen ? "收起研究依据" : "查看研究依据"}
        </button>
        {evidenceOpen &&
          topics.map((topic) => (
            <section key={topic.id} id={topic.id} className="op-research-topic">
              <button
                type="button"
                className="op-research-toggle"
                aria-expanded={openedTopics.includes(topic.id)}
                onClick={() =>
                  setOpenedTopics((previous) =>
                    previous.includes(topic.id)
                      ? previous.filter((id) => id !== topic.id)
                      : [...previous, topic.id],
                  )
                }
              >
                {topic.label}
              </button>
              {openedTopics.includes(topic.id) && (
                <div className="op-research-topic-body">
                  {topic.render()}
                  {topic.id === "research-strategy" &&
                    !!data.decision?.actions?.length && (
                      <div className="op-research-actions">
                        {data.decision.actions.map((action) => (
                          <span key={action.type}>{renderAction(action)}</span>
                        ))}
                      </div>
                    )}
                </div>
              )}
            </section>
          ))}
      </section>
      <section id="research-data" className="op-research-data">
        <button
          type="button"
          className="op-research-toggle"
          aria-expanded={dataOpen}
          onClick={() => setDataOpen(!dataOpen)}
        >
          数据与运行状态
        </button>
        {dataOpen && (
          <div
            className="op-research-data-body"
            role="region"
            aria-label="数据与运行状态详情"
          >
            {gapIssues.map(renderIssue)}
            <div className="op-research-collection">
              {board.dataStatus.collectionState && (
                <p>
                  采集记录状态：{stateLabel(board.dataStatus.collectionState)}
                </p>
              )}
              <p>
                最近处理记录：
                {board.dataStatus.collectedAt
                  ? timeLabel(board.dataStatus.collectedAt)
                  : "尚无处理记录"}
              </p>
              <p>
                下次处理时间：
                {board.dataStatus.nextCheckAt
                  ? timeLabel(board.dataStatus.nextCheckAt)
                  : "待确认"}
              </p>
              <small>处理记录与计划不代表采集任务此刻在线。</small>
              {Object.entries(board.dataStatus.sourceResults).map(
                ([source, result]) => (
                  <p key={source}>
                    {sourceLabels[source] ?? "其他已登记来源"}：
                    {stateLabel(result.status)}
                    {result.errorClass &&
                    /^[A-Za-z][A-Za-z0-9_.]{0,79}$/.test(result.errorClass)
                      ? ` · ${result.errorClass}`
                      : ""}
                  </p>
                ),
              )}
              <small>
                研究版本：
                {[
                  ...new Set([
                    board.methodVersion,
                    ...board.dataStatus.methodVersions,
                  ]),
                ].join(" · ")}
              </small>
            </div>
          </div>
        )}
      </section>
      <section className="op-research-tracking">
        <button
          type="button"
          className="op-research-toggle"
          aria-label="研究条件跟踪与通知"
          aria-expanded={trackingOpen}
          onClick={() => setTrackingOpen(!trackingOpen)}
        >
          研究条件跟踪与通知
          {data.notices?.some((n) => !n.readAt)
            ? `（${data.notices.filter((n) => !n.readAt).length}条未读）`
            : ""}
        </button>
        {trackingOpen && renderTracking()}
      </section>
    </section>
  );
}

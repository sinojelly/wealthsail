import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type {
  OpportunityContext,
  ResearchAction,
  ResearchIssue,
  Direction,
} from "../../../api/investment";
import { timeLabel } from "../macro-display";

import {
  stateLabel,
  categoryLabels,
  ownerLabels,
  sourceLabels,
  dataIssue,
  evidenceLabels,
  confirmedRisk,
  issueTone,
  toneClass,
  localRoutes,
  topicTargets,
} from "./research-board-display";
import ResearchBoardV2 from "./ResearchBoardV2";

export interface EvidenceTopic {
  id: string;
  label: string;
  render: () => ReactNode;
}

function LegacyResearchBoard({
  data,
  topics,
  canViewOperations = false,
  renderTracking,
  onTrack,
  trackingEnabled = false,
  directionLabel,
  hideTracking = false,
}: {
  data: OpportunityContext;
  topics: EvidenceTopic[];
  canViewOperations?: boolean;
  renderTracking: () => ReactNode;
  onTrack?: () => void;
  trackingEnabled?: boolean;
  directionLabel?: string;
  hideTracking?: boolean;
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
    if (hideTracking && action.type === "TRACK") return null;
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
      {!hideTracking && (
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
      )}
    </section>
  );
}

export interface ResearchBoardProps {
  data: OpportunityContext;
  topics: EvidenceTopic[];
  canViewOperations?: boolean;
  renderTracking: () => ReactNode;
  onTrack?: () => void;
  trackingEnabled?: boolean;
  directionLabel?: string;
  scopeKey?: string;
  onIndexSelect?: (id: string) => void;
  directions?: Direction[];
  onDirectionSelect?: (id: string) => void;
  hideTracking?: boolean;
  renderStudyTracking?: () => ReactNode;
  onStudyEvent?: (reportRunId: string, eventId: string) => void;
  studyPending?: boolean;
  studyError?: string;
}

export default function ResearchBoard(props: ResearchBoardProps) {
  return props.data.researchBoard?.methodVersion === "research-board-v2" ? (
    <ResearchBoardV2 key={props.scopeKey} {...props} />
  ) : (
    <LegacyResearchBoard {...props} />
  );
}

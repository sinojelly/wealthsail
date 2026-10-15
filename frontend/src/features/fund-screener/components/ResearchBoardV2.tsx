import { useState } from "react";
import { Link } from "react-router-dom";
import type { ResearchAction, ResearchIssue } from "../../../api/investment";
import Dialog from "../../../components/accessibility/Dialog";
import { sourceHref, timeLabel } from "../macro-display";
import type { ResearchBoardProps } from "./ResearchBoard";
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

type Detail =
  | { mode: "ISSUES"; target: string }
  | { mode: "DIRECTION" | "DATA" | "EVIDENCE" | "TRACKING" }
  | null;
const groupDefaults = [
  ["MARKET", "指数与基金数据"],
  ["MACRO", "宏观数据"],
  ["PERMISSION", "来源许可"],
  ["PORTFOLIO", "个人持仓"],
  ["STRATEGY", "策略验证"],
] as const;
const httpLabel = (value: unknown) =>
  typeof value === "number" &&
  Number.isInteger(value) &&
  value >= 100 &&
  value <= 599
    ? ` · HTTP ${value}`
    : "";

export default function ResearchBoardV2({
  data,
  topics,
  canViewOperations = false,
  renderTracking,
  onTrack,
  trackingEnabled = false,
  directionLabel,
  onIndexSelect,
}: ResearchBoardProps) {
  const board = data.researchBoard!;
  const [detail, setDetail] = useState<Detail>(null);
  const [topicId, setTopicId] = useState<string | null>(null);
  const [groupId, setGroupId] = useState<string | null>(null);
  const items = [
    ...new Map(board.items.map((item) => [item.id, item])).values(),
  ];
  const groups = groupDefaults.map(
    ([id, label]) =>
      board.dataStatus.groups?.find((group) => group.id === id) ?? {
        id,
        label,
        summary: "未列出独立受限事项",
        impact: "不代表完整研究资格已认证",
        owner: "RESEARCH_DATA",
        itemIds: [],
      },
  );
  const activeSummary =
    detail?.mode === "ISSUES"
      ? board.summaries.find((summary) => summary.id === detail.target)
      : undefined;
  const title =
    detail?.mode === "ISSUES"
      ? (activeSummary?.label ?? "研究事项")
      : detail?.mode === "DIRECTION"
        ? "当前方向研究"
        : detail?.mode === "DATA"
          ? "研究限制"
          : detail?.mode === "TRACKING"
            ? "研究条件跟踪与通知"
            : "研究依据";
  const activeGroup = groups.find((group) => group.id === groupId);
  const activeTopic = topics.find((topic) => topic.id === topicId);
  function evidence(id: string) {
    const macro = data.items.find((item) => item.code === id);
    const series = data.series?.find((item) => item.code === id);
    const index = data.marketEvidence?.find((item) => item.indexId === id);
    const event = data.events?.find((item) => item.id === id);
    return {
      label:
        macro?.label ??
        series?.label ??
        index?.label ??
        event?.title ??
        evidenceLabels[id] ??
        "其他关联证据",
      href: sourceHref(
        macro?.sourceUri ??
          series?.sourceUri ??
          index?.sourceUri ??
          event?.sourceUri,
      ),
    };
  }
  function open(next: Detail) {
    setDetail(next);
    setTopicId(null);
    setGroupId(null);
  }
  function perform(action: ResearchAction) {
    if (action.type === "VIEW_ISSUES")
      open({ mode: "ISSUES", target: action.target ?? "CHANGES" });
    if (action.type === "VIEW_DIRECTION") open({ mode: "DIRECTION" });
    if (action.type === "VIEW_DATA") open({ mode: "DATA" });
    if (action.type === "VIEW_EVIDENCE") {
      if (action.target === "research-data") open({ mode: "DATA" });
      else {
        open({ mode: "EVIDENCE" });
        if (action.target && topicTargets.includes(action.target))
          setTopicId(action.target);
      }
    }
    if (action.type === "VIEW_CANDIDATES") {
      setDetail(null);
      const target = document.getElementById("op-research-targets");
      target?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      target?.focus({ preventScroll: true });
    }
    if (action.type === "TRACK") {
      open({ mode: "TRACKING" });
      onTrack?.();
    }
  }
  function renderAction(action: ResearchAction, admin = false) {
    if (action.type === "VIEW_OPERATIONS") {
      return admin &&
        canViewOperations &&
        action.enabled &&
        action.href === localRoutes.VIEW_OPERATIONS ? (
        <Link to="/operations">{action.label}</Link>
      ) : null;
    }
    const route = localRoutes[action.type];
    if (route && action.enabled && action.href === route)
      return <Link to={route}>{action.label}</Link>;
    const validLocal = [
      "VIEW_ISSUES",
      "VIEW_DIRECTION",
      "VIEW_DATA",
      "VIEW_EVIDENCE",
      "VIEW_CANDIDATES",
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
              : (action.disabledReason ?? "当前操作暂不可用。")}
          </small>
        )}
      </>
    );
  }
  function renderIssue(issue: ResearchIssue) {
    const tone = issueTone(issue, data);
    const source = issue.blocker?.sourceKey
      ? board.dataStatus.sourceResults[issue.blocker.sourceKey]
      : undefined;
    const index = data.marketEvidence?.find(
      (index) => index.indexId === issue.affectedAssets.indexId,
    );
    return (
      <article key={issue.id} className={`op-v2-issue ${toneClass(tone)}`}>
        <div className="op-research-issue-heading">
          <h3>{issue.summary}</h3>
          <span className="op-badge">
            {categoryLabels[issue.category]} ·{" "}
            {issue.factVerification === "VERIFIED"
              ? "事实已核验"
              : "事实待核验"}
          </span>
        </div>
        <p className={toneClass(tone)}>{issue.impact}</p>
        {index && (
          <p>
            关联指数：<span>{index.label}</span>
          </p>
        )}
        {issue.affectedAssets.fundCode && (
          <p>
            关联基金：{issue.affectedAssets.fundCode}{" "}
            {issue.affectedAssets.fundMarket ?? "市场待核验"}
          </p>
        )}
        <small>
          影响判断：
          {issue.confidence === "VERIFIED"
            ? "证据已核验"
            : issue.confidence === "PARTIAL"
              ? "影响仍需部分核查"
              : "尚未确认"}
        </small>
        {issue.blocker && (
          <div className="op-research-blocker">
            <p>
              当前状态：{stateLabel(issue.blocker.state)} · 影响判断：
              {issue.blocker.affectedJudgment}
            </p>
            {issue.blocker.reason !== issue.impact && (
              <p>{issue.blocker.reason}</p>
            )}
            <p>
              处理责任：{ownerLabels[issue.blocker.owner] ?? "相关数据服务"} ·{" "}
              {issue.blocker.userFixable ? "可按操作补充" : "由负责模块核查"}
            </p>
            <p>
              来源：
              {issue.blocker.sourceKey?.startsWith("INDEX_RESEARCH:")
                ? "指数研究采集"
                : (sourceLabels[issue.blocker.sourceKey ?? ""] ??
                  "尚未登记具体来源")}
              {source ? ` · ${stateLabel(source.status)}` : ""}
              {source?.errorClass &&
              /^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(source.errorClass)
                ? ` · ${source.errorClass}`
                : ""}
              {httpLabel(source?.httpStatus)}
            </p>
            <p>
              最近处理：
              {issue.blocker.lastAttemptAt
                ? timeLabel(issue.blocker.lastAttemptAt)
                : "未检测到执行记录"}{" "}
              · 下次处理：
              {issue.blocker.nextCheckAt
                ? timeLabel(issue.blocker.nextCheckAt)
                : "待确认"}
            </p>
          </div>
        )}
        {!!issue.evidenceIds.length && (
          <div className="op-v2-provenance">
            {issue.evidenceIds.map((id) => {
              const ref = evidence(id);
              return ref.href ? (
                <a key={id} href={ref.href} target="_blank" rel="noreferrer">
                  查看{ref.label}来源
                </a>
              ) : (
                <small key={id}>关联证据：{ref.label}</small>
              );
            })}
          </div>
        )}
        {issue.lastChangedAt && (
          <small>实际变化记录：{timeLabel(issue.lastChangedAt)}</small>
        )}
        <div className="op-research-actions">
          {renderAction(issue.nextAction)}
        </div>
      </article>
    );
  }
  return (
    <section
      className="op-panel op-research-board op-research-board-v2"
      aria-label="市场环境与投资关注"
    >
      <header>
        <h2>市场环境与投资关注</h2>
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
              <p className={toneClass(tone)}>{summary.summary}</p>
              <span className="op-muted">{stateLabel(summary.state)}</span>
              <div className="op-research-actions">
                {renderAction(summary.nextAction)}
              </div>
            </section>
          );
        })}
      </div>
      <section
        className="op-research-direction op-v2-direction"
        aria-label="当前研究方向"
      >
        <div>
          <h3>{directionLabel ?? board.direction.label}</h3>
          <span className="op-badge">
            {stateLabel(board.direction.researchState)} ·{" "}
            {stateLabel(board.direction.qualificationState)}
          </span>
        </div>
        <p>{board.direction.summary ?? "已知事实与完整研究资格需分别核查。"}</p>
        {board.direction.nextStep && (
          <p className="op-muted">下一步：{board.direction.nextStep}</p>
        )}
        <div className="op-research-actions">
          <button type="button" onClick={() => open({ mode: "DIRECTION" })}>
            查看方向关联事项
          </button>
          <button type="button" onClick={() => open({ mode: "EVIDENCE" })}>
            查看研究依据
          </button>
          <button type="button" onClick={() => open({ mode: "DATA" })}>
            查看研究限制
          </button>
          <button
            type="button"
            aria-label="研究条件跟踪与通知"
            onClick={() => open({ mode: "TRACKING" })}
          >
            研究条件跟踪与通知
            {data.notices?.some((notice) => !notice.readAt)
              ? `（${data.notices.filter((notice) => !notice.readAt).length}条未读）`
              : ""}
          </button>
        </div>
      </section>
      <Dialog
        key={title}
        open={detail !== null}
        title={title}
        onClose={() => setDetail(null)}
        size="xl"
      >
        <div className="op-v2-detail-body">
          {detail?.mode === "ISSUES" && (
            <section aria-label="研究事项详情">
              {items
                .filter(
                  (item) =>
                    !dataIssue(item) &&
                    activeSummary?.itemIds.includes(item.id),
                )
                .map(renderIssue)}
              {!activeSummary?.itemIds.length && (
                <p>当前范围暂无已确认事项，仍可继续查看研究依据。</p>
              )}
            </section>
          )}
          {detail?.mode === "DIRECTION" && (
            <section
              className="op-v2-direction-detail"
              aria-label="方向研究详情"
            >
              <h3>{directionLabel ?? board.direction.label}</h3>
              {![
                ...board.direction.supports,
                ...board.direction.counterFactors,
              ].some((row) => row.summary === board.direction.summary) && (
                <p>{board.direction.summary}</p>
              )}
              <h3>支持依据</h3>
              {board.direction.supports.length ? (
                board.direction.supports.map((row, index) => (
                  <div key={index}>
                    <p>{row.summary}</p>
                    <small>
                      {stateLabel(row.state)} ·{" "}
                      {row.evidenceIds
                        .map((id) => evidence(id).label)
                        .join("、")}
                    </small>
                  </div>
                ))
              ) : (
                <p>尚无足够证据列出支持因素。</p>
              )}
              <h3>逆风与反方条件</h3>
              {board.direction.counterFactors.length ? (
                board.direction.counterFactors.map((row, index) => (
                  <div key={index}>
                    <p>{row.summary}</p>
                    <small>{stateLabel(row.state)}</small>
                  </div>
                ))
              ) : (
                <p>尚未形成独立反方结论。</p>
              )}
              <h3>当前规则条件</h3>
              {board.direction.failedConditions?.length ? (
                board.direction.failedConditions.map((condition) => (
                  <div key={condition.id}>
                    <strong>{condition.label}</strong>
                    <p>{condition.reason}</p>
                  </div>
                ))
              ) : (
                <p>未列出已核实但尚未满足的规则；不代表完整建仓资格已通过。</p>
              )}
              <h3>相关指数</h3>
              {(board.direction.relatedIndexIds ?? []).map((id) => {
                const index = data.marketEvidence?.find(
                  (index) => index.indexId === id,
                );
                return index ? (
                  <button
                    type="button"
                    key={id}
                    disabled={!onIndexSelect}
                    onClick={() => {
                      setDetail(null);
                      onIndexSelect?.(id);
                    }}
                  >
                    研究{index.label}
                  </button>
                ) : null;
              })}
              <h3>进一步研究</h3>
              {board.direction.recommendedChecks.map((check) => (
                <p key={check}>{check}</p>
              ))}
              <p>{board.direction.nextStep}</p>
              {!!board.direction.blockerIds?.length && (
                <button type="button" onClick={() => open({ mode: "DATA" })}>
                  查看研究限制
                </button>
              )}
            </section>
          )}
          {detail?.mode === "DATA" && (
            <section aria-label="研究限制分组">
              <div className="op-v2-table-scroll">
                <table className="op-v2-limitations">
                  <thead>
                    <tr>
                      <th>受限类型</th>
                      <th>具体项目</th>
                      <th>影响</th>
                      <th>处理责任</th>
                      <th>详情</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groups.map((group) => (
                      <tr key={group.id}>
                        <th scope="row">{group.label}</th>
                        <td>{group.summary}</td>
                        <td>{group.impact}</td>
                        <td>{ownerLabels[group.owner] ?? "相关数据服务"}</td>
                        <td>
                          <button
                            type="button"
                            aria-label={`${groupId === group.id ? "收起" : "展开"}${group.label}`}
                            aria-expanded={groupId === group.id}
                            onClick={() =>
                              setGroupId(groupId === group.id ? null : group.id)
                            }
                          >
                            查看
                            {group.itemIds.length
                              ? `（${group.itemIds.length}）`
                              : ""}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {activeGroup && (
                <section
                  className="op-v2-group-children"
                  aria-label={`${activeGroup.label}受限项目`}
                >
                  <h3>{activeGroup.label}</h3>
                  {items
                    .filter(
                      (item) =>
                        dataIssue(item) &&
                        activeGroup.itemIds.includes(item.id),
                    )
                    .map(renderIssue)}
                  {!activeGroup.itemIds.length && (
                    <p>未列出独立受限事项，不代表当前证据已完整评估。</p>
                  )}
                </section>
              )}
              <footer className="op-research-collection">
                <p>
                  最近采集记录：
                  {board.dataStatus.collectedAt
                    ? timeLabel(board.dataStatus.collectedAt)
                    : "未检测到执行记录"}{" "}
                  · 下次计划：
                  {board.dataStatus.nextCheckAt
                    ? timeLabel(board.dataStatus.nextCheckAt)
                    : "待确认"}
                </p>
                <small>
                  实际记录与计划不代表采集任务此刻在线；分组不代表同一故障根因。
                </small>
                <small>
                  研究版本：{board.dataStatus.methodVersions.join(" · ")}
                </small>
                {board.dataStatus.adminAction && (
                  <div className="op-v2-admin-action">
                    {renderAction(board.dataStatus.adminAction, true)}
                  </div>
                )}
              </footer>
            </section>
          )}
          {detail?.mode === "EVIDENCE" && (
            <section aria-label="研究依据">
              <nav className="op-v2-topic-nav" aria-label="研究专题">
                {topics.map((topic) => (
                  <button
                    type="button"
                    key={topic.id}
                    aria-pressed={topicId === topic.id}
                    onClick={() =>
                      setTopicId(topicId === topic.id ? null : topic.id)
                    }
                  >
                    {topic.label}
                  </button>
                ))}
              </nav>
              {activeTopic ? (
                <div className="op-research-topic-body">
                  {activeTopic.render()}
                  {activeTopic.id === "research-strategy" && (
                    <div className="op-research-actions">
                      {data.decision?.actions?.map((action) => (
                        <span key={action.type}>{renderAction(action)}</span>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <p>选择一个研究专题查看实际证据、历史与来源。</p>
              )}
            </section>
          )}
          {detail?.mode === "TRACKING" && renderTracking()}
        </div>
      </Dialog>
    </section>
  );
}

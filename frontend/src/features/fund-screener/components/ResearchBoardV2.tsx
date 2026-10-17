import { useEffect, useId, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronUp, Globe2 } from "lucide-react";
import { Link } from "react-router-dom";
import type {
  ResearchAction,
  ResearchIssue,
  ResearchTab,
} from "../../../api/investment";
import DailyResearchReport, {
  DailyResearchStatus,
} from "./DailyResearchReport";
import { sourceHref, timeLabel } from "../macro-display";
import ResearchFocus from "./ResearchFocus";
import { readingText } from "../research-reading";
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
} from "./research-board-display";

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
  onIndexSelect,
  directions,
  onDirectionSelect,
  renderStudyTracking,
  onStudyEvent,
  studyPending,
  studyError,
}: ResearchBoardProps) {
  const board = data.researchBoard!;
  const [params, setParams] = useSearchParams();
  const panelId = useId();
  const focusCategory = useRef(false);
  const expanded = params.get("researchOpen") === "1";
  const tabs = [
    ["events", "今日重点"],
    ["macro", "市场证据"],
    ["tracking", "我的跟踪与复盘"],
  ] as const;
  const tab =
    (params.get("researchTab") === "quality" ||
    params.get("researchData") === "1"
      ? "macro"
      : tabs.find(([id]) => id === params.get("researchTab"))?.[0]) ?? "events";
  const qualityOpen =
    params.get("researchTab") === "quality" ||
    params.get("researchData") === "1";
  useEffect(() => {
    if (expanded && focusCategory.current) {
      focusCategory.current = false;
      document.getElementById(`${panelId}-${tab}`)?.focus();
    }
  }, [expanded, tab, panelId]);
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
  const activeGroup = groups.find((group) => group.id === groupId);
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
  function open(target: ResearchTab, expanded = true) {
    setGroupId(null);
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (expanded) next.set("researchOpen", "1");
      else next.delete("researchOpen");
      next.set(
        "researchTab",
        target === "quality"
          ? "macro"
          : target === "direction"
            ? "events"
            : target,
      );
      if (target === "quality") next.set("researchData", "1");
      else next.delete("researchData");
      return next;
    });
  }
  function perform(action: ResearchAction) {
    focusCategory.current = true;
    if (action.type === "VIEW_DIRECTION") researchIndex();
    if (action.type === "VIEW_ISSUES") open("events");
    if (action.type === "VIEW_DATA") open("quality");
    if (action.type === "VIEW_EVIDENCE")
      open(
        action.target === "research-data"
          ? "quality"
          : action.target === "research-macro"
            ? "macro"
            : action.target === "research-global"
              ? "events"
              : "direction",
      );
    if (action.type === "VIEW_CANDIDATES") researchIndex();
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
    ].includes(action.type);
    const disabled = !action.enabled || !validLocal;
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
          <small>{action.disabledReason ?? "当前操作暂不可用。"}</small>
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

  function researchIndex(id?: string) {
    if (id) onIndexSelect?.(id);
    requestAnimationFrame(() => {
      const target = document.getElementById("op-research-targets");
      target?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      target?.focus({ preventScroll: true });
    });
  }
  const reportTime = data.dailyResearch?.report
    ? (data.dailyResearch.reportCompletedAt ??
      data.dailyResearch.asOf ??
      data.dailyResearch.report.asOf)
    : null;
  const critical = items.filter((item) => confirmedRisk(item, data));
  const observationIds = new Set(
    data.researchOverview?.highlights.map((item) => item.id) ?? [],
  );
  return (
    <section
      className={`op-panel op-research-board op-research-board-v2 op-daily-observation ${expanded ? "is-expanded" : "is-collapsed"}`}
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
          <span>
            <Globe2 size={19} />
            <strong>今日投资观察</strong>
          </span>
          <span>
            <span className="op-observation-control">
              {expanded ? "收起研究" : "展开研究"}
            </span>
            {expanded ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
          </span>
        </button>
        <div className="op-observation-meta">
          <span className="op-badge">
            <DailyResearchStatus
              state={data.dailyResearch?.state}
              scheduleState={data.dailyResearch?.scheduleState}
              hasReport={!!data.dailyResearch?.report}
            />
          </span>
          <small>
            {reportTime
              ? `最近有效研究：${timeLabel(reportTime)}`
              : "暂无有效日报"}{" "}
            · 下次计划：{timeLabel(data.dailyResearch?.nextRunAt)}
          </small>
        </div>
        <p className="op-daily-summary">
          {readingText(
            data.researchOverview?.summary ??
              "尚无足够证据形成可靠研究判断，可展开核查已存事实与下一条件。",
            220,
          )}
        </p>
      </header>
      {!!critical.length && (
        <div className="op-observation-critical">
          {critical.map(renderIssue)}
        </div>
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
            className="op-research-topic-body op-v2-detail-body"
          >
            {tab === "events" && (
              <>
                <p className="op-section-guide">
                  先看变化和下一条件，再到下方工作台研究具体指数与基金；值得持续观察的判断可加入跟踪。
                </p>
                <DailyResearchReport
                  data={data.dailyResearch}
                  directions={directions}
                  onDirectionSelect={onDirectionSelect}
                  onTrack={onStudyEvent}
                  trackingPending={studyPending}
                  trackingError={studyError}
                >
                  <ResearchFocus data={data} onResearchIndex={researchIndex} />
                </DailyResearchReport>
                <details className="op-official-observations">
                  <summary>官方发布与其他观察</summary>
                  {topics
                    .find((topic) => topic.id === "research-global")
                    ?.render()}
                  {items
                    .filter(
                      (item) =>
                        !dataIssue(item) &&
                        !confirmedRisk(item, data) &&
                        !observationIds.has(item.id) &&
                        !item.key.startsWith("trend:") &&
                        !item.key.startsWith("pe:"),
                    )
                    .map(renderIssue)}
                </details>
                <p className="op-holdings-status">
                  {board.summaries.some(
                    (row) =>
                      row.id === "HOLDINGS" &&
                      ["CHECKED", "BREACH"].includes(row.state),
                  )
                    ? "持仓风险仍按具体标的和个人预算独立核查。"
                    : "个人持仓风险尚未核验，不能认定安全。"}{" "}
                  <Link to="/dashboard?scope=MINE">核对我的持仓</Link>
                </p>
              </>
            )}
            {tab === "macro" && (
              <>
                <p className="op-section-guide">
                  按经济问题核查证据；选择指标看走势与解释，来源及数据缺口按需展开。
                </p>
                <button
                  type="button"
                  className="op-data-toggle"
                  aria-expanded={qualityOpen}
                  onClick={() =>
                    qualityOpen ? open("macro") : open("quality")
                  }
                >
                  数据状态
                </button>
                {!qualityOpen &&
                  topics
                    .find((topic) => topic.id === "research-macro")
                    ?.render()}
              </>
            )}
            {tab === "tracking" &&
              (renderStudyTracking?.() ?? (
                <p>
                  从事件或当前标的保存初始研究，后续核对已存证据；旧标的条件监控保持独立。
                </p>
              ))}
            {tab === "macro" && qualityOpen && (
              <>
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
                            <td>
                              {ownerLabels[group.owner] ?? "相关数据服务"}
                            </td>
                            <td>
                              <button
                                type="button"
                                aria-label={`${groupId === group.id ? "收起" : "展开"}${group.label}`}
                                aria-expanded={groupId === group.id}
                                onClick={() =>
                                  setGroupId(
                                    groupId === group.id ? null : group.id,
                                  )
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
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

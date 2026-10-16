import { useEffect, useId, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ChevronDown, Globe2 } from "lucide-react";
import { Link } from "react-router-dom";
import type {
  ResearchAction,
  ResearchIssue,
  ResearchTab,
  OverviewItem,
} from "../../../api/investment";
import DailyResearchReport, {
  DailyResearchStatus,
} from "./DailyResearchReport";
import { monthLabel, sourceHref, timeLabel } from "../macro-display";
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
  directionLabel,
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
    ["events", "重大事件与情景"],
    ["macro", "宏观与市场证据"],
    ["direction", "投资方向与基金"],
    ["tracking", "跟踪与复盘"],
  ] as const;
  const tab =
    (params.get("researchTab") === "quality"
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
      next.set("researchTab", target === "quality" ? "macro" : target);
      if (target === "quality") next.set("researchData", "1");
      else next.delete("researchData");
      return next;
    });
  }
  function perform(action: ResearchAction) {
    focusCategory.current = true;
    if (action.type === "VIEW_DIRECTION") open("direction");
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
    if (action.type === "VIEW_CANDIDATES") {
      const target = document.getElementById("op-research-targets");
      target?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      target?.focus({ preventScroll: true });
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
  const fallback = items
    .filter((item) => !dataIssue(item))
    .map((item) => ({
      id: item.id,
      kind: item.category,
      title: item.summary,
      summary: "",
      impact: item.impact,
      tone: issueTone(item, data),
      targetTab: "events" as ResearchTab,
      indexId: item.affectedAssets.indexId ?? null,
      directionIds: [],
      sourceIds: [],
      asOf:
        data.marketEvidence?.find(
          (row) => row.indexId === item.affectedAssets.indexId,
        )?.asOf ?? null,
    }));
  const highlights = (data.researchOverview?.highlights ?? fallback).slice(
    0,
    3,
  );
  function highlightTone(highlight: OverviewItem) {
    return highlight.tone === "RED" &&
      !items.some(
        (item) => item.id === highlight.id && confirmedRisk(item, data),
      )
      ? "GRAY"
      : highlight.tone;
  }
  return (
    <section
      className={`op-panel op-research-board op-research-board-v2 op-daily-observation ${expanded ? "is-expanded" : "is-collapsed"}`}
      aria-label="今日投资观察"
    >
      <button
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
          <span className="op-badge">
            <DailyResearchStatus state={data.dailyResearch?.state} />
          </span>
          <ChevronDown size={16} />
        </span>
      </button>
      <small className="op-daily-asof">
        最近有效研究：
        {timeLabel(
          data.dailyResearch?.reportCompletedAt ??
            data.dailyResearch?.asOf ??
            data.dailyResearch?.report?.asOf,
        )}{" "}
        · 下次计划：{timeLabel(data.dailyResearch?.nextRunAt)}
      </small>
      {data.researchOverview?.summary && (
        <p className="op-daily-summary">{data.researchOverview.summary}</p>
      )}
      {!expanded && (
        <>
          {data.researchOverview?.groups ? (
            <div className="op-observation-groups">
              {data.researchOverview.groups.map((group) => (
                <section key={group.id} aria-label={group.label}>
                  <h3>{group.label}</h3>
                  {!group.items.length && <p>{group.summary}</p>}
                  {!!group.items.length && (
                    <ul>
                      {group.items.slice(0, 2).map((highlight) => (
                        <li key={highlight.id}>
                          <button
                            type="button"
                            className={toneClass(highlightTone(highlight))}
                            onClick={() => open(highlight.targetTab)}
                          >
                            {highlight.title}
                          </button>
                          <p
                            className={
                              highlightTone(highlight) === "RED"
                                ? undefined
                                : "op-observation-summary"
                            }
                          >
                            {highlight.summary}
                          </p>
                          {highlightTone(highlight) === "RED" &&
                            highlight.impact &&
                            highlight.impact !== highlight.summary && (
                              <p
                                className={
                                  highlightTone(highlight) === "RED"
                                    ? "op-critical-impact"
                                    : undefined
                                }
                              >
                                {highlight.impact}
                              </p>
                            )}
                          <small>
                            {highlight.scope === "GLOBAL"
                              ? "全局事项"
                              : highlight.scope === "HOLDINGS"
                                ? "个人持仓"
                                : "当前选择"}{" "}
                            ·{" "}
                            {(
                              {
                                EVENT_LEAD: "事件线索",
                                DIRECTION_LEAD: "方向线索",
                                INDEX_CONDITION: "指数条件",
                                FUND_TOOL: "基金工具",
                                EXECUTION_CONDITION: "执行条件",
                              } as Record<string, string>
                            )[highlight.stage ?? ""] ?? "研究线索"}
                            {highlight.phase
                              ? ` · ${({ PLANNED: "计划中", ANNOUNCED: "已宣布", IMPLEMENTED: "已实施", REPORTED: "报道线索", OPINION: "观点", OBSERVING: "观察中", CONFIRMED: "已确认" } as Record<string, string>)[highlight.phase] ?? "阶段待核查"}`
                              : ""}
                            {highlight.asOf
                              ? ` · 依据期：${/^\d{4}-\d{2}$/.test(highlight.asOf) ? monthLabel(highlight.asOf) : timeLabel(highlight.asOf)}`
                              : ""}
                          </small>
                          {highlight.nextAction && (
                            <div className="op-research-actions">
                              {renderAction(highlight.nextAction)}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  {group.nextAction && (
                    <div className="op-research-actions">
                      {renderAction(group.nextAction)}
                    </div>
                  )}
                </section>
              ))}
            </div>
          ) : (
            <>
              {highlights.length ? (
                <ul className="op-daily-highlights" aria-label="今日投资重点">
                  {highlights.map((highlight) => (
                    <li key={highlight.id}>
                      <button
                        type="button"
                        onClick={() =>
                          open(
                            tabs.some(([id]) => id === highlight.targetTab)
                              ? highlight.targetTab
                              : "events",
                          )
                        }
                        className={toneClass(highlightTone(highlight))}
                      >
                        {highlight.title}
                      </button>
                      {highlight.summary &&
                        highlight.summary !== highlight.title && (
                          <p>{highlight.summary}</p>
                        )}
                      {highlight.impact &&
                        highlight.impact !== highlight.summary && (
                          <p
                            className={
                              highlightTone(highlight) === "RED"
                                ? "op-critical-impact"
                                : undefined
                            }
                          >
                            {highlight.impact}
                          </p>
                        )}
                      {highlight.asOf && (
                        <small>
                          依据期：
                          {/^\d{4}-\d{2}$/.test(highlight.asOf)
                            ? monthLabel(highlight.asOf)
                            : timeLabel(highlight.asOf)}
                        </small>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="op-muted op-daily-empty">
                  当前暂无足够证据列出投资重点；可展开查看已采集事实，个人风险仍需独立核查。
                </p>
              )}
            </>
          )}
        </>
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
                type="button"
                role="tab"
                aria-selected={tab === id}
                aria-controls={`${panelId}-content`}
                tabIndex={tab === id ? 0 : -1}
                onClick={() => open(id)}
                onKeyDown={(event) => {
                  const offset =
                    event.key === "ArrowRight"
                      ? 1
                      : event.key === "ArrowLeft"
                        ? -1
                        : 0;
                  if (!offset && event.key !== "Home" && event.key !== "End")
                    return;
                  event.preventDefault();
                  const next =
                    event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? tabs.length - 1
                        : (i + offset + tabs.length) % tabs.length;
                  open(tabs[next][0]);
                  (
                    event.currentTarget.parentElement?.querySelectorAll(
                      "button",
                    )[next] as HTMLButtonElement | undefined
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
            {items.filter((item) => confirmedRisk(item, data)).map(renderIssue)}
            {tab === "events" && (
              <>
                <DailyResearchReport
                  data={data.dailyResearch}
                  directions={directions}
                  onDirectionSelect={onDirectionSelect}
                  onTrack={onStudyEvent}
                  trackingPending={studyPending}
                />
                {studyError && <p role="alert">{studyError}</p>}
                {topics
                  .find((topic) => topic.id === "research-global")
                  ?.render()}
                {items
                  .filter(
                    (item) => !dataIssue(item) && !confirmedRisk(item, data),
                  )
                  .map(renderIssue)}
              </>
            )}
            {tab === "macro" && (
              <>
                <button
                  type="button"
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
            {tab === "direction" && (
              <>
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
                    <p>
                      未列出已核实但尚未满足的规则；不代表完整建仓资格已通过。
                    </p>
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
                          open("direction", false);
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
                    <button type="button" onClick={() => open("quality")}>
                      查看研究限制
                    </button>
                  )}
                </section>
                {topics
                  .find((topic) => topic.id === "research-market")
                  ?.render()}
                {topics
                  .find((topic) => topic.id === "research-strategy")
                  ?.render()}
                {!!data.decision?.actions?.length && (
                  <div className="op-research-actions">
                    {data.decision.actions.map((action) => (
                      <span key={action.type}>{renderAction(action)}</span>
                    ))}
                  </div>
                )}
              </>
            )}
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

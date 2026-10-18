import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import * as api from "../../../api/study-api";
import { useStudyTracking } from "../use-study-tracking";
import { metricValue, sourceHref, timeLabel } from "../macro-display";
import {
  studyStates as states,
  studyActions as actions,
  studyVerdicts as verdicts,
  studyErrors as errors,
  studyMetricLabel as metricLabel,
  studySubjectLabel as subjectLabel,
  studyConditionLabel as conditionLabel,
} from "./study-display";
import StudyVersionEditor from "./StudyVersionEditor";
import ActionablePlan from "./ActionablePlan";
import Decimal from "decimal.js";
import type { ActionableZoneResult } from "../../../api/investment";
import { planPrice, planUnit } from "../plan-display";

function excursionLabel(value: string | null | undefined) {
  if (value == null || value.trim() === "" || !Number.isFinite(Number(value)))
    return "—";
  return `${new Decimal(value).toFixed(2, Decimal.ROUND_HALF_UP)}%`;
}
export interface StudyTrackingProps {
  actor: string;
  scope: api.StudyScope;
  origin?: api.CreateStudy | null;
  preview?: {
    direction?: string;
    subject: string;
    code?: string;
    plan?: ActionableZoneResult;
  };
}
export default function StudyTracking(props: StudyTrackingProps) {
  const [params] = useSearchParams();
  return (
    <StudyWorkspace
      key={`${props.actor}:${JSON.stringify(props.scope)}:${params.get("studyTrack") ?? "new"}`}
      {...props}
    />
  );
}
function StudyWorkspace({ actor, scope, origin, preview }: StudyTrackingProps) {
  const [params, setParams] = useSearchParams();
  const id = params.get("studyTrack");
  const [offset, setOffset] = useState(0);
  const { list, detail, command } = useStudyTracking(actor, id, offset);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [horizon, setHorizon] = useState(30);
  const [editing, setEditing] = useState(false);
  const [selectedVersion, setSelectedVersion] =
    useState<api.StudyVersion | null>(null);
  const [decision, setDecision] = useState<keyof typeof actions>("WATCH");
  const [decisionReason, setDecisionReason] = useState("");
  const [verdict, setVerdict] = useState<keyof typeof verdicts>("INCONCLUSIVE");
  const [reviewReason, setReviewReason] = useState("");
  const [lessons, setLessons] = useState("");
  const [errorCategories, setErrorCategories] = useState<string[]>([]);
  const [showTrades, setShowTrades] = useState(false);
  const [fillId, setFillId] = useState("");
  const [tradeReason, setTradeReason] = useState("");
  const trades = useQuery({
    queryKey: ["study", actor, "eligible-trades", id],
    queryFn: ({ signal }) => api.getEligibleStudyTrades(id!, signal),
    enabled: !!id && showTrades,
  });
  const value = detail.data;
  const current = value?.version;
  const version = selectedVersion ?? current;
  const snapshot = version?.snapshot;
  function choose(next: string) {
    setOffset(0);
    setSelectedVersion(null);
    setEditing(false);
    setShowTrades(false);
    setParams((previous) => {
      const copy = new URLSearchParams(previous);
      copy.set("studyTrack", next);
      return copy;
    });
  }
  const run = (action: () => Promise<api.StudyDetail>) =>
    command.mutate(action);
  return (
    <section className="op-study" aria-label="研究主题跟踪与复盘">
      <h3>研究主题跟踪与复盘</h3>
      <p>
        冻结初始假设、价格依据与来源，后续核对条件并复盘。计划与真实成交分别记录。
      </p>
      {list.isPending && <p role="status">正在读取我的研究主题…</p>}
      {list.isError && (
        <p role="alert">
          主题列表读取失败。
          <button onClick={() => void list.refetch()}>重试主题列表</button>
        </p>
      )}
      {list.data && !list.data.items.length && (
        <p>
          暂无我的研究主题；可从今日重点或当前标的保存研究，也可选择新建研究。
        </p>
      )}
      <nav className="op-study-list" aria-label="我的研究主题">
        {list.data?.items.map((track) => (
          <div key={track.id}>
            <button
              type="button"
              aria-pressed={id === track.id}
              onClick={() => choose(track.id)}
            >
              {track.title} · {states[track.status] ?? "待核对"}
              {track.unreadCount ? ` · ${track.unreadCount}项未读变化` : ""}
            </button>
            <p className="op-study-next">
              下一条件：
              {id === track.id && value?.track.id === track.id && snapshot
                ? (snapshot.watchFor[0] ??
                  (snapshot.conditions[0]
                    ? conditionLabel(snapshot.conditions[0])
                    : "未声明下一条件，需人工复盘。"))
                : "选择主题后读取冻结条件；列表未提供条件详情。"}
            </p>
          </div>
        ))}
      </nav>
      <details className="op-study-create-toggle">
        <summary>新建研究</summary>
        <form
          className="op-study-create"
          onSubmit={async (event) => {
            event.preventDefault();
            const result = await command
              .mutateAsync(() =>
                api.createStudyTrack({
                  ...(origin ?? { originType: "SELECTION", scope }),
                  horizonDays: horizon,
                }),
              )
              .catch(() => null);
            if (result && alive.current) choose(result.track.id);
          }}
        >
          <p>
            {origin?.originType === "DAILY_EVENT"
              ? "来源：当前展示的有效日报事件；保存原始报告身份与研究快照。"
              : "来源：当前方向、指数或基金选择；不自动替换已有研究。"}
          </p>
          <section aria-label="即将跟踪的研究对象">
            <strong>
              {preview?.direction ??
                (scope.directionId ? "当前所选研究方向" : "标的研究")}
            </strong>
            <p>
              {preview?.subject ??
                (scope.indexId
                  ? subjectLabel(scope.indexId)
                  : scope.fundCode
                    ? "当前所选基金"
                    : scope.instrumentId
                      ? "当前所选基金"
                      : "尚未选择具体研究对象")}{" "}
              ·{" "}
              {preview?.code ??
                scope.fundCode ??
                scope.indexId ??
                scope.instrumentId ??
                "未选择"}
            </p>
            {preview?.plan ? (
              <>
                <p>
                  当前{preview.plan.priceBasis === "UNIT_NAV" ? "净值" : "点位"}
                  ：{planPrice(preview.plan.currentPrice, preview.plan)}{" "}
                  {planUnit(preview.plan.unit)}
                </p>
                {preview.plan.tracks.map((track) => (
                  <p key={track.track}>
                    {track.track === "LEFT_VALUE" ? "左侧低吸" : "右侧趋势"}
                    区间：{planPrice(track.entryZoneMin, preview.plan!)}–
                    {planPrice(track.entryZoneMax, preview.plan!)}
                  </p>
                ))}
              </>
            ) : (
              <p>当前价格与买入区间尚无有效依据；保存时由服务器核对。</p>
            )}
            <small>只读预览；保存时冻结服务端读取的原始证据。</small>
          </section>
          <label>
            观察天数
            <input
              aria-label="观察天数"
              type="number"
              min={1}
              max={365}
              value={horizon}
              onChange={(event) => setHorizon(Number(event.target.value))}
            />
          </label>
          <button
            disabled={
              command.isPending ||
              !Number.isInteger(horizon) ||
              horizon < 1 ||
              horizon > 365 ||
              (!origin && !Object.values(scope).some(Boolean))
            }
          >
            冻结研究并跟踪
          </button>
        </form>
      </details>
      {command.isError && (
        <p role="alert">
          {command.error instanceof Error
            ? command.error.message
            : "保存失败，请重新读取当前版本后重试。"}
          <button
            type="button"
            onClick={() => {
              command.reset();
              void detail.refetch();
            }}
          >
            重新读取当前版本
          </button>
        </p>
      )}
      {detail.isPending && id && <p role="status">正在读取冻结研究…</p>}
      {detail.isError && (
        <p role="alert">
          研究详情读取失败或无权访问。
          <button onClick={() => void detail.refetch()}>重新读取研究</button>
        </p>
      )}
      {value && snapshot && current && (
        <>
          <header>
            <h3>{value.track.title}</h3>
            <p>
              {states[value.track.status] ?? "状态待核对"} · 版本{" "}
              {version!.version} · 保存：{timeLabel(version!.createdAt)} ·
              观察至 {timeLabel(snapshot.expiresOn)}
            </p>
          </header>
          <div className="op-research-actions">
            <button
              disabled={command.isPending || !value.capabilities.canCheck}
              onClick={() =>
                run(() =>
                  api.checkStudy(value.track.id, value.track.generation),
                )
              }
            >
              核对已存证据
            </button>
            <button
              disabled={command.isPending || !value.capabilities.canEdit}
              onClick={() =>
                run(() =>
                  api.setStudyStatus(value.track.id, {
                    expectedGeneration: value.track.generation,
                    enabled: !value.track.enabled,
                  }),
                )
              }
            >
              {value.track.enabled ? "停止主题跟踪" : "恢复主题跟踪"}
            </button>
            <button
              disabled={command.isPending || !value.capabilities.canEdit}
              onClick={() => setEditing(!editing)}
            >
              修订研究条件
            </button>
            <button
              disabled={command.isPending || !value.track.unreadCount}
              onClick={() =>
                run(() =>
                  api.addStudyNote(value.track.id, {
                    expectedGeneration: value.track.generation,
                    kind: "ACK",
                  }),
                )
              }
            >
              标记主题变化已读
            </button>
          </div>
          <section aria-label="冻结研究快照">
            <h4>原始研究与版本快照</h4>
            {snapshot.phase && (
              <p>
                事件阶段：
                {(
                  {
                    PLANNED: "计划中",
                    ANNOUNCED: "已宣布",
                    IMPLEMENTED: "已实施",
                    REPORTED: "报道线索",
                    OPINION: "观点",
                  } as Record<string, string>
                )[snapshot.phase] ?? "阶段待核查"}
              </p>
            )}
            {snapshot.scenarios?.map((scenario, index) => (
              <section key={index} aria-label={`冻结情景${index + 1}`}>
                <h5>原始条件情景{index + 1}（模型推演）</h5>
                <p>{scenario.condition}</p>
                <p>{scenario.impact}</p>
                <p>{scenario.counter}</p>
              </section>
            ))}
            <p>{snapshot.thesis}</p>
            {value?.excursions &&
              (!selectedVersion || selectedVersion.id === current?.id) && (
                <section aria-label="行情研究表现">
                  <h5>冻结计划后的行情研究表现</h5>
                  <p>
                    同口径行情偏移，不是实际盈亏；不含个人仓位、成交费用或现金流。
                  </p>
                  <p>
                    最大有利偏移（MFE）：
                    {excursionLabel(value.excursions.mfePct)}
                  </p>
                  <p>
                    最大不利偏移（MAE）：
                    {excursionLabel(value.excursions.maePct)}
                  </p>
                  <p>
                    {value.excursions.sampleCount ?? "未知"}个实际样本 ·{" "}
                    {timeLabel(value.excursions.baseAsOf)}至
                    {timeLabel(value.excursions.lastAsOf)} · 口径：
                    {value.excursions.priceBasis ?? "未确认"}
                  </p>
                  {(value.excursions.mfePct == null ||
                    value.excursions.maePct == null) && (
                    <p>行情证据尚未齐备，未知偏移不视为零。</p>
                  )}
                  {value.excursions.reasons?.map((reason, index) => (
                    <p key={index}>{reason}</p>
                  ))}
                </section>
              )}
            {snapshot.actionablePlan && (
              <ActionablePlan result={snapshot.actionablePlan} frozen />
            )}
            {!!snapshot.unknowns?.length && (
              <section aria-label="原始研究未知事项">
                <h5>原始研究中的未知</h5>
                {snapshot.unknowns.map((reason, index) => (
                  <p key={index}>{reason}</p>
                ))}
              </section>
            )}
            {(
              [
                ["传导假设", snapshot.transmission],
                ["支持依据", snapshot.supports],
                ["反方因素", snapshot.counterFactors],
                ["待观察", snapshot.watchFor],
                ["失效条件", snapshot.invalidationConditions],
              ] as const
            ).map(([label, rows]) => (
              <div key={label}>
                <strong>{label}</strong>
                {rows.length ? (
                  rows.map((row, i) => <p key={i}>{row}</p>)
                ) : (
                  <p>未声明，需人工核查。</p>
                )}
              </div>
            ))}
            <small>
              冻结报告：{snapshot.origin.reportRunId ?? "当前选择"} · 研究时间：
              {timeLabel(snapshot.origin.reportAsOf ?? snapshot.createdAt)} ·
              基准：
              {snapshot.benchmarkIndexId
                ? subjectLabel(snapshot.benchmarkIndexId)
                : "未设定可比基准"}
            </small>
            {snapshot.sources.map((source) => (
              <div key={source.id}>
                <p>
                  {sourceHref(source.url) ? (
                    <a
                      href={sourceHref(source.url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {source.title ?? "原始研究来源"}
                    </a>
                  ) : (
                    source.title
                  )}{" "}
                  · 公布：
                  {timeLabel(source.publishedAt ?? source.publishedDate)} ·
                  获取：{timeLabel(source.retrievedAt)}
                  ；原文核对不认证研究推理。
                </p>
                {source.quote && <blockquote>{source.quote}</blockquote>}
              </div>
            ))}
            {!!snapshot.observations.length && (
              <details>
                <summary>冻结时的实际观察与来源</summary>
                {snapshot.observations.map((row, i) => (
                  <p key={i}>
                    {metricLabel(row.metric)} {subjectLabel(row.subjectKey)}：
                    {row.value == null ? "—" : metricValue(Number(row.value))}{" "}
                    {row.unit} · 观察：{timeLabel(row.asOf)} · 获取：
                    {timeLabel(row.retrievedAt)} ·{" "}
                    {states[row.state ?? ""] ?? "待核对"}{" "}
                    {sourceHref(row.sourceUri) && (
                      <a
                        href={sourceHref(row.sourceUri)}
                        target="_blank"
                        rel="noreferrer"
                      >
                        原始事实来源
                      </a>
                    )}
                  </p>
                ))}
              </details>
            )}
            {!snapshot.conditions.length && (
              <p>
                未声明可执行数值规则；自然语言假设保留为人工复盘，不能自动判定证伪。
              </p>
            )}
            {!!snapshot.conditions.length && (
              <>
                <section aria-label="研究验证条件">
                  <h5>研究验证条件</h5>
                  {snapshot.conditions.map((condition, i) => (
                    <p key={i}>{conditionLabel(condition)}</p>
                  ))}
                </section>
                <details>
                  <summary>原始条件依据</summary>
                  {snapshot.conditions.map((condition, i) => (
                    <p key={i}>
                      {condition.metric} {condition.subjectKey ?? ""}{" "}
                      {condition.operator} {condition.threshold}{" "}
                      {condition.unit} · {condition.effect}
                    </p>
                  ))}
                </details>
              </>
            )}
          </section>
          {editing && (
            <StudyVersionEditor
              key={current.id}
              current={current}
              busy={command.isPending}
              save={(body) =>
                run(() =>
                  api.updateStudyVersion(value.track.id, {
                    ...body,
                    expectedGeneration: value.track.generation,
                  }),
                )
              }
            />
          )}
          <section aria-label="研究时间线">
            <h4>核对记录与历史版本</h4>
            {value.versions.map((row) => (
              <button key={row.id} onClick={() => setSelectedVersion(row)}>
                查看版本{row.version} · {timeLabel(row.createdAt)}
              </button>
            ))}
            {value.checks.map((check) => (
              <article key={check.id}>
                <strong>
                  {timeLabel(check.evaluatedAt)} ·{" "}
                  {states[check.result.state] ?? "状态待核对"}
                </strong>
                <p>
                  {check.result.baseline
                    ? "首次建立基线，不作为新触发。"
                    : "使用已存证据核对，不运行模型或抓取外网。"}
                </p>
                {check.result.quality === "UNKNOWN" && (
                  <p>
                    无法核验，保留此前已核实状态及日期：
                    {timeLabel(check.result.stateAsOf)}
                  </p>
                )}
                {check.result.conditionResults.map((row) => (
                  <p key={row.id}>
                    {metricLabel(row.metric)}：{states[row.result] ?? "待核对"}{" "}
                    · {metricValue(row.value)} {row.unit} ·{" "}
                    {timeLabel(row.asOf)}
                  </p>
                ))}
              </article>
            ))}
            {value.notes.map((note) => (
              <article key={note.id}>
                <strong>
                  {timeLabel(note.createdAt)} ·{" "}
                  {note.kind === "DECISION"
                    ? "决策记录（非成交）"
                    : note.kind === "REVIEW"
                      ? "人工复盘"
                      : note.kind === "LIFECYCLE"
                        ? `主题生命周期记录：${({ CREATED: "创建研究", VERSION_CREATED: "修订版本", RESUMED: "恢复跟踪", STOPPED: "停止跟踪" } as Record<string, string>)[note.payload.operation ?? ""] ?? "启停或版本变化"}`
                        : "已读记录"}
                </strong>
                <p>
                  {actions[note.payload.action as keyof typeof actions] ??
                    verdicts[note.payload.verdict as keyof typeof verdicts] ??
                    ""}{" "}
                  {note.payload.reason}
                </p>
                {note.payload.lessons && <p>{note.payload.lessons}</p>}
                {note.payload.errorCategories?.length && (
                  <p>
                    误差归因：
                    {note.payload.errorCategories
                      .map(
                        (code) =>
                          errors[code as keyof typeof errors] ?? "待核查",
                      )
                      .join("、")}
                  </p>
                )}
              </article>
            ))}
            {offset > 0 && (
              <button onClick={() => setOffset(Math.max(0, offset - 50))}>
                较新的记录
              </button>
            )}
            {value.history.hasMore && (
              <button onClick={() => setOffset(offset + 50)}>更早的记录</button>
            )}
          </section>
          {value.review && (
            <section aria-label="证据复盘">
              <h4>初始与最新事实</h4>
              {version!.id !== current.id && (
                <p>
                  下方事实对照基于当前版本{current.version}
                  ，不属于上方正在查看的历史版本{version!.version}。
                </p>
              )}
              {value.review.observations.map((row, i) => (
                <p key={i}>
                  {metricLabel(row.metric)}：初始{" "}
                  {metricValue(row.initialValue)}（{timeLabel(row.initialAsOf)}
                  ）→ 最新 {metricValue(row.currentValue)}（
                  {timeLabel(row.currentAsOf)}）{row.unit} · 变化{" "}
                  {metricValue(row.change)}
                </p>
              ))}
              <p>
                净收益：{metricValue(value.review.performance.netReturn)} ·
                超额收益：{metricValue(value.review.performance.excessReturn)} ·
                最大回撤：{metricValue(value.review.performance.maxDrawdown)} ·
                机会成本：
                {metricValue(value.review.performance.opportunityCost)}
              </p>
              {value.review.performance.reasons.map((reason) => (
                <p key={reason}>{reason}</p>
              ))}
              <p>研究与价格变化的因果归因尚未核实。</p>
            </section>
          )}
          <details className="op-study-record-operations">
            <summary>记录与复盘操作</summary>
            <section aria-label="决策与人工复盘">
              <h4>记录决策与复盘</h4>
              <p>
                正在复盘版本{version!.version}
                ；记录关联此冻结版本，保留当前主题的版本保护。
              </p>
              {selectedVersion && selectedVersion.id !== current.id && (
                <button
                  type="button"
                  onClick={() => {
                    setSelectedVersion(null);
                    setEditing(false);
                  }}
                >
                  返回当前版本{current.version}
                </button>
              )}
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  run(() =>
                    api.addStudyNote(value.track.id, {
                      expectedGeneration: value.track.generation,
                      kind: "DECISION",
                      versionId: version!.id,
                      action: decision,
                      reason: decisionReason,
                    }),
                  );
                }}
              >
                <label>
                  决策记录
                  <select
                    aria-label="决策记录"
                    value={decision}
                    onChange={(event) =>
                      setDecision(event.target.value as keyof typeof actions)
                    }
                  >
                    {Object.entries(actions).map(([key, label]) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  决策原因
                  <textarea
                    aria-label="决策原因"
                    required
                    value={decisionReason}
                    onChange={(event) => setDecisionReason(event.target.value)}
                  />
                </label>
                <button disabled={command.isPending || !decisionReason.trim()}>
                  保存决策记录
                </button>
              </form>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  run(() =>
                    api.addStudyNote(value.track.id, {
                      expectedGeneration: value.track.generation,
                      kind: "REVIEW",
                      versionId: version!.id,
                      verdict,
                      reason: reviewReason,
                      lessons,
                      errorCategories,
                    }),
                  );
                }}
              >
                <label>
                  人工结论
                  <select
                    aria-label="人工结论"
                    value={verdict}
                    onChange={(event) =>
                      setVerdict(event.target.value as keyof typeof verdicts)
                    }
                  >
                    {Object.entries(verdicts).map(([key, label]) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  复盘原因
                  <textarea
                    aria-label="复盘原因"
                    required
                    value={reviewReason}
                    onChange={(event) => setReviewReason(event.target.value)}
                  />
                </label>
                <label>
                  经验与修正
                  <textarea
                    aria-label="经验与修正"
                    value={lessons}
                    onChange={(event) => setLessons(event.target.value)}
                  />
                </label>
                <fieldset>
                  <legend>误差归因（可多选）</legend>
                  {Object.entries(errors).map(([key, label]) => (
                    <label key={key}>
                      <input
                        type="checkbox"
                        checked={errorCategories.includes(key)}
                        onChange={(event) =>
                          setErrorCategories(
                            event.target.checked
                              ? [...errorCategories, key]
                              : errorCategories.filter((code) => code !== key),
                          )
                        }
                      />
                      {label}
                    </label>
                  ))}
                </fieldset>
                <button disabled={command.isPending || !reviewReason.trim()}>
                  保存人工复盘
                </button>
              </form>
            </section>
            <section aria-label="真实成交关联">
              <h4>真实成交</h4>
              <p>
                计划记录不是成交。仅能关联本人有权限且身份匹配的已确认记录；成交时间精度为日，同日无法判断先后。
              </p>
              <button
                disabled={!value.capabilities.canLinkTrade}
                onClick={() => setShowTrades(!showTrades)}
              >
                关联真实成交
              </button>
              {showTrades && (
                <>
                  {trades.isPending && <p>正在核查可关联成交…</p>}
                  {trades.isError && (
                    <p role="alert">成交权限或记录读取失败。</p>
                  )}
                  {trades.data && !trades.data.items.length && (
                    <p>暂无有权限且身份匹配的已确认成交。</p>
                  )}
                  {!!trades.data?.items.length && (
                    <form
                      onSubmit={(event) => {
                        event.preventDefault();
                        run(() =>
                          api.linkStudyTrade(value.track.id, {
                            expectedGeneration: value.track.generation,
                            fillId,
                            versionId: version!.id,
                            reason: tradeReason,
                          }),
                        );
                      }}
                    >
                      <label>
                        已确认成交
                        <select
                          required
                          aria-label="已确认成交"
                          value={fillId}
                          onChange={(event) => setFillId(event.target.value)}
                        >
                          <option value="">请选择真实成交</option>
                          {trades.data.items
                            .filter((fill) => fill.status === "CONFIRMED")
                            .map((fill) => (
                              <option key={fill.fillId} value={fill.fillId}>
                                {fill.tradeDate} {fill.instrumentName}{" "}
                                {fill.side === "BUY" ? "买入" : "卖出"}{" "}
                                {fill.quantity}份 · 单价{fill.unitPrice}{" "}
                                {fill.currency ?? "币种待核验"} · 费用
                                {fill.fee} {fill.currency ?? "币种待核验"}
                              </option>
                            ))}
                        </select>
                      </label>
                      <label>
                        关联原因
                        <textarea
                          aria-label="关联原因"
                          required
                          value={tradeReason}
                          onChange={(event) =>
                            setTradeReason(event.target.value)
                          }
                        />
                      </label>
                      <button
                        disabled={
                          command.isPending || !fillId || !tradeReason.trim()
                        }
                      >
                        确认关联成交
                      </button>
                    </form>
                  )}
                </>
              )}
              {value.tradeLinks.map((link) => (
                <article key={link.id}>
                  <p>
                    {link.fill.tradeDate} {link.fill.instrumentName} · 数量{" "}
                    {link.fill.quantity} · 单价 {link.fill.unitPrice}{" "}
                    {link.fill.currency ?? "币种待核验"} · 费用 {link.fill.fee}{" "}
                    {link.fill.currency ?? "币种待核验"}
                  </p>
                  <small>
                    {link.timingRelation === "BEFORE_TRADE"
                      ? "研究版本早于成交日"
                      : link.timingRelation === "POST_HOC"
                        ? "事后关联：研究晚于成交日"
                        : "同日或先后未知，不认证事前决策"}{" "}
                    · 成交仅有日期精度
                  </small>
                </article>
              ))}
            </section>
          </details>
        </>
      )}
    </section>
  );
}

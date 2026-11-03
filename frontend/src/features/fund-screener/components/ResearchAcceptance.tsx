import { useContext, useMemo, useRef, useState } from "react";
import {
  QueryClientContext,
  useMutation,
  useQuery,
} from "@tanstack/react-query";
import * as api from "../../../api/research-acceptance";
import { HttpError } from "../../../api/client";
import {
  getSessionSubject,
  privateQueryKey,
  useAuthSession,
} from "../../../app/auth-session";
import { sourceHref, timeLabel } from "../macro-display";
import { readingText } from "../research-reading";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const componentLabels: Record<string, string> = {
  sources: "来源",
  industryCycleReviews: "行业核查",
  valueDirections: "观察方向",
  events: "已发生事件",
  upcoming: "后续日程",
  fundPlans: "基金计划",
  plans: "基金计划",
  report: "报告",
};
const itemLabels: Record<string, string> = {
  REPORT: "报告",
  SOURCE: "来源",
  INDUSTRY_REVIEW: "行业核查",
  VALUE_DIRECTION: "观察方向",
  FUND_PLAN: "基金计划",
  EVENT: "事件",
  UPCOMING: "日程",
};
const dispositionLabels: Record<string, string> = {
  QUARANTINED: "已隔离",
  BLOCKED: "已阻断",
  REPAIRED: "已修复",
};
const artifactLabels = {
  PACKET: "冻结输入",
  RAW_MODEL: "模型原文",
  SOURCE_CHECKS: "来源核验凭据",
};
const archiveLabels = {
  AUDIT_PENDING: "待归档",
  ARCHIVING: "归档恢复中",
  ARCHIVED: "已归档",
  UNRECOVERABLE: "无法自动恢复",
};
const archivePending = (state?: string) =>
  state === "AUDIT_PENDING" || state === "ARCHIVING";

function ArchiveRecheck({
  checking,
  failed,
  onRecheck,
}: {
  checking: boolean;
  failed: boolean;
  onRecheck: () => void;
}) {
  return (
    <div>
      <p role="status">
        {failed
          ? "验收归档状态暂不可读取。"
          : "归档可能仍在恢复，可稍后重新检查。"}
      </p>
      <button
        type="button"
        style={actionStyle}
        disabled={checking}
        onClick={onRecheck}
      >
        重新检查归档状态
      </button>
      <p>
        <small>只读取已保存状态，不会重新执行研究。</small>
      </p>
    </div>
  );
}
const cellStyle = { padding: "8px", borderBottom: "1px solid var(--op-line)" };
const actionStyle = {
  border: "1px solid var(--op-line)",
  borderRadius: "6px",
  padding: "8px 12px",
  width: "fit-content",
  maxWidth: "100%",
};
const riskLabels: Record<string, string> = {
  FINANCING_COOLING: "融资降温",
  LAYOFFS_CLOSURES: "欠薪倒闭",
  ORDER_CUTS: "下游砍单",
  OVERCAPACITY_PRICE_WAR: "产能价格战",
  PRODUCTION_DELAY: "量产推迟",
  EARLY_COMMERCIALIZATION: "商业化早期，兑现待验证",
  NO_EARNINGS_DELIVERY: "缺乏当期盈利兑现",
  NO_CURRENT_EARNINGS_PROOF: "当期盈利兑现缺少原文实证",
  FORWARD_PLANS_ONLY: "仅见远期扩产或框架意向",
  PERSISTED_RISK_WATCH: "延续近期行业降温观察",
};
const gapLabels: Record<string, string> = {
  CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING: "当期利润增长或真实回款证据不足",
  FORWARD_PLANS_ONLY: "仅见远期扩产或框架意向，尚无当期盈利兑现",
  NO_CURRENT_EARNINGS_PROOF: "当期盈利兑现缺少原文实证",
  EARNINGS_INDUSTRY_SCOPE_UNVERIFIED: "公司财务证据与当前产业方向的关联待核实",
  STAGE_INDUSTRY_SCOPE_UNVERIFIED: "产业阶段证据与当前产业方向的关联待核实",
};

function RiskSummary({ cycle }: { cycle?: api.AcceptanceIndustryRisk | null }) {
  if (!cycle) return null;
  const level = {
    NONE: "本轮未确认负面",
    UNPROVEN: "兑现待验证",
    SUSPECTED: "待核实",
    CONFIRMED: "已确认",
  }[cycle.riskLevel ?? "NONE"];
  const flags = [
    ...new Set([
      ...(cycle.confirmedRiskFlags ?? []),
      ...(cycle.suspectedRiskFlags ?? []),
      ...(cycle.persistedRiskFlags ?? []),
      ...(cycle.riskFlags ?? []),
    ]),
  ];
  const gaps = [...new Set(cycle.evidenceGaps ?? [])].map((gap) => {
    const scopeRisk = gap.startsWith("INDUSTRY_SCOPE_UNVERIFIED:")
      ? riskLabels[gap.slice("INDUSTRY_SCOPE_UNVERIFIED:".length)]
      : undefined;
    return scopeRisk
      ? `${scopeRisk}证据的行业归属待核实`
      : (gapLabels[gap] ??
          (riskLabels[gap]
            ? `${riskLabels[gap]}证据仍待补齐`
            : readingText(gap, 300)));
  });
  return (
    <div aria-label="观察行业风险">
      <p>
        <strong>行业风险：{cycle.riskLevel ? level : "待核查"}</strong>
      </p>
      {flags.length > 0 && (
        <p>
          未解决风险：
          {flags
            .map((flag) => riskLabels[flag] ?? readingText(flag, 200))
            .join("；")}
        </p>
      )}
      {gaps.length > 0 && <p>证据缺口：{gaps.join("；")}</p>}
    </div>
  );
}

export default function ResearchAcceptance({
  runId,
  state,
  completedAt,
}: {
  runId?: string | null;
  state?: string | null;
  completedAt?: string | null;
}) {
  const client = useContext(QueryClientContext);
  const session = useAuthSession();
  // Isolated legacy report renderers do not own a query client or a session.
  if (!client || !session || !runId || !uuid.test(runId)) return null;
  const actor = getSessionSubject(session);
  return (
    <AcceptanceData
      key={`${actor}:${session.role}:${runId}`}
      runId={runId}
      actor={actor}
      state={state}
      completedAt={completedAt}
      queryKey={privateQueryKey(
        session,
        "research-acceptance",
        session.role,
        runId,
      )}
    />
  );
}

function AcceptanceData({
  runId,
  actor,
  queryKey,
  state,
  completedAt,
}: {
  runId: string;
  actor: string;
  queryKey: readonly (string | number)[];
  state?: string | null;
  completedAt?: string | null;
}) {
  const archiveFetches = useRef({ lifecycle: "", count: 0 });
  const lifecycle = `${state ?? "UNKNOWN"}:${completedAt ?? "undated"}`;
  const terminal =
    state === "READY" ||
    state === "FAILED" ||
    (state === "UNCERTAIN" &&
      !!completedAt &&
      Number.isFinite(Date.parse(completedAt)));
  const query = useQuery({
    queryKey: [
      ...queryKey,
      "initial",
      state ?? "UNKNOWN",
      completedAt ?? "undated",
    ],
    queryFn: ({ signal }) => {
      if (archiveFetches.current.lifecycle !== lifecycle)
        archiveFetches.current = { lifecycle, count: 0 };
      archiveFetches.current.count += 1;
      return api.getResearchAcceptance(runId, signal);
    },
    enabled: terminal,
    retry: (failureCount, error) =>
      failureCount < 2 &&
      (error instanceof TypeError ||
        (error instanceof HttpError &&
          (error.status === 408 ||
            error.status === 429 ||
            error.status >= 500))),
    retryDelay: 1000,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: (query) =>
      !query.state.error &&
      archivePending(query.state.data?.archiveState) &&
      archiveFetches.current.count < 20
        ? 15000
        : false,
  });
  if (!terminal) return null;
  if (query.error instanceof HttpError && query.error.status === 404)
    return null;
  const recheck = (
    <ArchiveRecheck
      checking={query.isFetching}
      failed={query.isError}
      onRecheck={() => {
        void query.refetch();
      }}
    />
  );
  const data = query.data;
  if (!data)
    return query.isError ? (
      <section
        aria-label="研究验收记录"
        className="op-daily-acceptance op-research-issues"
      >
        {recheck}
      </section>
    ) : null;
  if (data.researchId !== runId || data.kind !== "INITIAL") return null;
  return (
    <section
      aria-label="研究验收记录"
      className="op-daily-acceptance op-research-issues"
      style={{
        border: "1px solid var(--op-line)",
        borderRadius: "8px",
        overflowWrap: "anywhere",
      }}
    >
      {data.archiveState && <p>归档状态：{archiveLabels[data.archiveState]}</p>}
      {data.archiveState && data.archiveState !== "ARCHIVED" ? (
        <div role="status">
          <h4>
            {data.archiveState === "UNRECOVERABLE"
              ? "验收归档无法自动恢复"
              : "验收归档待恢复"}
          </h4>
          <p>
            {data.archiveMessage ??
              (data.archiveState === "UNRECOVERABLE"
                ? "验收归档无法自动恢复，不影响已发布严格报告。请管理员核对已保存材料。"
                : "验收归档待恢复，不影响已发布严格报告。")}
          </p>
          <p>研究执行：{data.researchId}</p>
        </div>
      ) : (
        <Outcome data={data} />
      )}
      {(query.isError ||
        (data.archiveState && data.archiveState !== "ARCHIVED")) &&
        recheck}
      {(data.canInspectArtifacts || data.canReplay) && (
        <AdminTools
          key={`${actor}:${runId}`}
          data={data}
          actor={actor}
          queryKey={queryKey}
        />
      )}
    </section>
  );
}

function Outcome({
  data,
  replay = false,
}: {
  data: api.ResearchAcceptanceOutcome;
  replay?: boolean;
}) {
  const title =
    data.decision === "COMPLETE"
      ? "验收完整"
      : data.decision === "PARTIAL"
        ? "部分验收成果·仅供观察，未正式发布"
        : data.decision === "EMPTY"
          ? "本次验收无可展示成果·未正式发布"
          : "本次验收未通过·未正式发布";
  return (
    <>
      {data.decision === "COMPLETE" ? (
        <p>
          <span className="op-badge">
            {replay ? "离线重放验收完整" : title}
          </span>
        </p>
      ) : (
        <h4 style={{ fontWeight: 650 }}>
          {replay ? `离线重放：${title}` : title}
        </h4>
      )}
      <p style={{ fontSize: "12px", color: "var(--op-muted)" }}>
        研究执行：{data.researchId} · 校验版本：{data.validatorVersion} ·
        校验时间：{timeLabel(data.validatedAt)}
        {data.validatorRevision &&
          /^[a-f0-9]{64}$/i.test(data.validatorRevision) && (
            <span
              title={
                data.canInspectArtifacts ? data.validatorRevision : undefined
              }
            >
              {" "}
              · 校验修订：{data.validatorRevision.slice(0, 12)}
            </span>
          )}
        {data.buildCommit &&
          /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/i.test(data.buildCommit) && (
            <span
              title={data.canInspectArtifacts ? data.buildCommit : undefined}
            >
              {" "}
              · 构建版本：
              {data.buildCommit.slice(
                0,
                data.buildCommit.length === 40 ? 7 : 12,
              )}
            </span>
          )}
      </p>
      {data.acceptedContentComplete === false && (
        <p
          role="note"
          style={{
            fontSize: "12px",
            fontWeight: 600,
            color: "var(--op-warning, #9a6700)",
          }}
        >
          验收正文为节选，完整内容请管理员查看冻结材料
        </p>
      )}
      {data.decision !== "COMPLETE" && (
        <>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "12px",
              textAlign: "left",
            }}
          >
            <caption style={{ textAlign: "left", marginBottom: "6px" }}>
              组件验收计数
            </caption>
            <thead>
              <tr>
                <th style={cellStyle}>类别</th>
                <th style={cellStyle}>输入</th>
                <th style={cellStyle}>通过</th>
                <th style={cellStyle}>隔离</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.counts).map(([name, count]) => (
                <tr key={name}>
                  <th scope="row" style={cellStyle}>
                    {componentLabels[name] ?? "其他组件"}
                  </th>
                  <td style={cellStyle}>{count.inputCount}</td>
                  <td style={cellStyle}>{count.acceptedCount}</td>
                  <td style={cellStyle}>{count.quarantinedCount}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Observations accepted={data.accepted} />
          <Issues data={data} />
        </>
      )}
      {data.decision === "COMPLETE" && data.issueCount > 0 && (
        <details>
          <summary>查看验收问题（{data.issueCount}项）</summary>
          <Issues data={data} />
        </details>
      )}
    </>
  );
}

function Observations({
  accepted,
}: {
  accepted: api.ResearchAcceptanceOutcome["accepted"];
}) {
  const observations = (title: string, rows: api.AcceptanceObservation[]) =>
    rows.length > 0 && (
      <section aria-label={title}>
        <h5>{title}</h5>
        <ul>
          {rows.map((row, index) => (
            <li key={row.id ?? row.key ?? index}>
              <strong>{row.title}</strong>
              {(row.thesis || row.summary) && (
                <p>{row.thesis ?? row.summary}</p>
              )}
              {(row.scheduledAt || row.scheduledDate) && (
                <p>
                  计划时间：{timeLabel(row.scheduledAt ?? row.scheduledDate)}
                </p>
              )}
              {row.industryCycle?.evidenceSummary && (
                <p>产业核查：{row.industryCycle.evidenceSummary}</p>
              )}
              <RiskSummary cycle={row.industryCycle} />
              {!!row.watchFor?.length && (
                <p>继续核查：{row.watchFor.join("；")}</p>
              )}
            </li>
          ))}
        </ul>
      </section>
    );
  return (
    <div aria-label="已验收观察内容" style={{ display: "grid", gap: "14px" }}>
      {accepted.macroOverview && (
        <section aria-label="宏观总纲·模型研判待核实">
          <h5>宏观总纲·模型研判待核实</h5>
          <p>仅供观察，非可交易建议。</p>
          {accepted.macroOverview.summary && (
            <p>{accepted.macroOverview.summary}</p>
          )}
          {accepted.macroOverview.macroCycle && (
            <p>{accepted.macroOverview.macroCycle}</p>
          )}
          {!!accepted.macroOverview.coverage?.length && (
            <ul>
              {accepted.macroOverview.coverage.map((row, index) => (
                <li key={`${row.topic}:${index}`}>
                  <strong>
                    {(
                      {
                        DOMESTIC_GROWTH: "国内增长与信用",
                        US_RATES: "美国通胀与利率",
                        ENERGY_SHIPPING: "能源与航运",
                        GEOPOLITICS_POLICY: "全球政策与地缘事件",
                        GLOBAL_EQUITIES: "全球权益",
                        CALENDAR: "未来日历",
                      } as Record<string, string>
                    )[row.topic] ?? "宏观覆盖"}{" "}
                    ·{" "}
                    {row.status === "FOUND"
                      ? "发现线索，待核实"
                      : row.status === "NOT_FOUND"
                        ? "本轮未检得"
                        : "尚未核验"}
                  </strong>
                  <p>{row.summary}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {observations("观察方向", accepted.valueDirections)}
      {accepted.industryCycleReviews.length > 0 && (
        <section aria-label="已验收行业核查">
          <h5>行业核查</h5>
          <ul>
            {accepted.industryCycleReviews.map((row, index) => (
              <li key={`${row.industry}:${index}`}>
                <strong>{row.industry}</strong>
                {row.evidenceSummary && <p>{row.evidenceSummary}</p>}
                <RiskSummary cycle={row} />
              </li>
            ))}
          </ul>
        </section>
      )}
      {observations("已验收事件", accepted.events)}
      {observations("已验收日程", accepted.upcoming)}
      {accepted.sources.length > 0 && (
        <section aria-label="已验收来源">
          <h5>来源</h5>
          <ul>
            {accepted.sources.map((source) => {
              const href = sourceHref(source.url);
              return (
                <li key={source.id}>
                  {href ? (
                    <a href={href} target="_blank" rel="noreferrer">
                      {source.title}
                    </a>
                  ) : (
                    <strong>{source.title}</strong>
                  )}
                  <p>
                    <span className="op-badge" aria-label="来源核验状态">
                      {source.verification === "SOURCE_CHECKED"
                        ? "原文已核对"
                        : source.verification === "UNAVAILABLE"
                          ? "原文暂不可用"
                          : source.verification === "UNVERIFIED"
                            ? "原文尚未核验"
                            : "来源状态待核实"}
                    </span>{" "}
                    {source.verification === "SOURCE_CHECKED"
                      ? "引用与原始发布匹配，不代表影响已兑现。"
                      : "原文与引用尚待核验，不能视为已确认事实。"}
                  </p>
                  {source.quote && <blockquote>{source.quote}</blockquote>}
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}

function Issues({ data }: { data: api.ResearchAcceptanceOutcome }) {
  const [itemType, setItemType] = useState("ALL");
  const [rule, setRule] = useState("ALL");
  const issues = useMemo(
    () =>
      (data.issues ?? []).filter(
        (issue) => issue.researchId === data.researchId,
      ),
    [data.issues, data.researchId],
  );
  const types = useMemo(
    () => [...new Set(issues.map((issue) => issue.itemType))].sort(),
    [issues],
  );
  const rules = useMemo(
    () =>
      [
        ...new Set(
          issues
            .map((issue) => issue.ruleId)
            .filter((value): value is string => !!value),
        ),
      ].sort(),
    [issues],
  );
  const visible = issues.filter(
    (issue) =>
      (itemType === "ALL" || issue.itemType === itemType) &&
      (rule === "ALL" || issue.ruleId === rule),
  );
  const admin = data.canInspectArtifacts || data.canReplay;
  return (
    <section aria-label="验收问题清单">
      <h5>问题清单（共{data.issueCount}项）</h5>
      {(!data.issueListComplete || issues.length !== data.issueCount) && (
        <p>问题清单未完整保存，请核对原始产物。</p>
      )}
      {issues.length > 0 && (
        <>
          <label style={{ display: "grid", gap: "6px", minWidth: 0 }}>
            条目类别
            <select
              aria-label="条目类别"
              style={{
                maxWidth: "100%",
                padding: "7px",
                border: "1px solid var(--op-line)",
              }}
              value={itemType}
              onChange={(event) => setItemType(event.target.value)}
            >
              <option value="ALL">全部类别</option>
              {types.map((type) => (
                <option key={type} value={type}>
                  {itemLabels[type] ?? "其他条目"}
                </option>
              ))}
            </select>
          </label>
          <label style={{ display: "grid", gap: "6px", minWidth: 0 }}>
            规则代码
            <select
              aria-label="规则代码"
              style={{
                maxWidth: "100%",
                padding: "7px",
                border: "1px solid var(--op-line)",
              }}
              value={rule}
              onChange={(event) => setRule(event.target.value)}
            >
              <option value="ALL">全部规则</option>
              {rules.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <p>当前显示{visible.length}项</p>
          <ol style={{ display: "grid", gap: "12px" }}>
            {visible.map((issue) => (
              <li
                key={issue.issueId}
                style={{
                  borderLeft: "2px solid var(--op-line)",
                  paddingLeft: "10px",
                }}
              >
                <p>
                  <strong>{itemLabels[issue.itemType] ?? "其他条目"}</strong> ·{" "}
                  {dispositionLabels[issue.disposition] ?? "待核对"} · 问题ID：
                  {issue.issueId}
                </p>
                <p>{issue.message}</p>
                {typeof issue.itemIndex === "number" &&
                  Number.isSafeInteger(issue.itemIndex) &&
                  issue.itemIndex >= 0 &&
                  issue.itemIndex <= 9999 && (
                    <p>条目索引：{issue.itemIndex}（从0开始）</p>
                  )}
                {issue.path && <p>位置：{issue.path}</p>}
                {issue.ruleId && <p>规则：{issue.ruleId}</p>}
                {issue.reasonCode && <p>原因：{issue.reasonCode}</p>}
                {admin && (
                  <>
                    {issue.businessKey && (
                      <p>
                        业务关联：<span>{issue.businessKey}</span>
                      </p>
                    )}
                    {!!issue.dependencies?.length && (
                      <p>
                        依赖：
                        {issue.dependencies
                          .map((dependency) =>
                            typeof dependency === "string"
                              ? dependency
                              : `${itemLabels[dependency.itemType] ?? dependency.itemType} · ${dependency.businessKey}`,
                          )
                          .join("、")}
                      </p>
                    )}
                    {!!issue.missingIndustryCodes?.length && (
                      <p>
                        缺失行业代码：{issue.missingIndustryCodes.join("、")}
                      </p>
                    )}
                  </>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function readCommand(key: string) {
  try {
    const value = sessionStorage.getItem(key);
    return value && uuid.test(value) ? value : null;
  } catch {
    return null;
  }
}

function AdminTools({
  data,
  actor,
  queryKey,
}: {
  data: api.ResearchAcceptanceOutcome;
  actor: string;
  queryKey: readonly (string | number)[];
}) {
  const [rawOpen, setRawOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const storageKey = `wealthsail:research-acceptance:replay:${actor}:${data.researchId}`;
  const [unconfirmedKey, setUnconfirmedKey] = useState(() =>
    readCommand(storageKey),
  );
  const commandKey = useRef<string | null>(unconfirmedKey);
  const artifacts = useQuery({
    queryKey: [...queryKey, "artifacts"],
    queryFn: ({ signal }) => api.getResearchArtifacts(data.researchId, signal),
    enabled: rawOpen && data.canInspectArtifacts,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const replays = useQuery({
    queryKey: [...queryKey, "replays"],
    queryFn: ({ signal }) => api.getResearchReplays(data.researchId, signal),
    enabled: historyOpen && data.canReplay,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const replay = useMutation({
    mutationFn: (key: string) => {
      commandKey.current = key;
      setUnconfirmedKey(key);
      try {
        sessionStorage.setItem(storageKey, key);
      } catch {
        /* A memory key still prevents duplicate retries during this mount. */
      }
      return api.replayResearchAcceptance(data.researchId, key);
    },
    retry: false,
    onSuccess: (value, key) => {
      if (value.researchId !== data.researchId || value.kind !== "REPLAY")
        return;
      try {
        if (readCommand(storageKey) === key)
          sessionStorage.removeItem(storageKey);
      } catch {
        /* Storage may be unavailable. */
      }
      if (commandKey.current === key) {
        commandKey.current = null;
        setUnconfirmedKey(null);
      }
    },
  });
  const latest =
    replay.data?.researchId === data.researchId && replay.data.kind === "REPLAY"
      ? replay.data
      : null;
  return (
    <section aria-label="管理员验收核查">
      <h5>管理员核查</h5>
      {data.recoverableMaterials && (
        <section aria-label="可恢复材料">
          <h6>可恢复材料</h6>
          <ul>
            {(
              Object.keys(artifactLabels) as Array<keyof typeof artifactLabels>
            ).map((kind) => (
              <li key={kind}>
                {artifactLabels[kind]}：
                {data.recoverableMaterials?.[kind] ? "可恢复" : "缺失或超限"}
              </li>
            ))}
          </ul>
        </section>
      )}
      {data.canInspectArtifacts && (
        <button
          type="button"
          style={actionStyle}
          onClick={() => setRawOpen(!rawOpen)}
          aria-expanded={rawOpen}
        >
          查看冻结输入与原始结果
        </button>
      )}
      {rawOpen && (
        <div aria-label="冻结研究产物">
          {artifacts.isPending && <p role="status">正在读取已保存产物…</p>}
          {artifacts.isError && <p role="status">原始产物暂不可读取。</p>}
          {artifacts.data?.researchId === data.researchId && (
            <>
              <p>关联研究：{artifacts.data.researchId}</p>
              {!artifacts.data.complete && (
                <p>部分原始产物缺失或超过保存上限，无法完整离线重放。</p>
              )}
              {artifacts.data.items.map((item) => (
                <details key={item.kind} open>
                  <summary>{artifactLabels[item.kind] ?? item.kind}</summary>
                  <p>大小：{item.byteLength ?? "未知"} bytes</p>
                  {item.sha256 && (
                    <p style={{ overflowWrap: "anywhere" }}>
                      SHA-256：{item.sha256}
                    </p>
                  )}
                  {typeof item.content === "string" ? (
                    <pre
                      aria-label={`${artifactLabels[item.kind] ?? item.kind}完整原文`}
                      style={{
                        maxHeight: "60vh",
                        overflow: "auto",
                        whiteSpace: "pre-wrap",
                        overflowWrap: "anywhere",
                      }}
                    >
                      {item.content}
                    </pre>
                  ) : (
                    <p>完整内容未保存：{item.omissionReason ?? "原因未记录"}</p>
                  )}
                </details>
              ))}
            </>
          )}
        </div>
      )}
      {data.canReplay && (
        <>
          <button
            type="button"
            style={actionStyle}
            disabled={replay.isPending}
            onClick={() =>
              replay.mutate(commandKey.current ?? crypto.randomUUID())
            }
          >
            {replay.isPending
              ? "正在离线校验…"
              : unconfirmedKey
                ? "重试离线重放校验（不调用Codex）"
                : "离线重放校验（不调用Codex）"}
          </button>
          <button
            type="button"
            style={actionStyle}
            onClick={() => setHistoryOpen(!historyOpen)}
            aria-expanded={historyOpen}
          >
            查看离线重放记录
          </button>
        </>
      )}
      {replay.isError && (
        <p role="status">离线重放结果尚未确认，可使用同一命令重试。</p>
      )}
      {latest && (
        <section aria-label="离线重放结果">
          <Outcome data={latest} replay />
        </section>
      )}
      {historyOpen && (
        <section aria-label="已保存离线重放记录">
          {replays.isPending && <p role="status">正在读取重放记录…</p>}
          {replays.isError && <p role="status">重放记录暂不可读取。</p>}
          {replays.data?.items
            .filter(
              (item) =>
                item.researchId === data.researchId &&
                item.kind === "REPLAY" &&
                item.id !== latest?.id,
            )
            .map((item) => (
              <details key={item.id} open>
                <summary>
                  {item.validatorVersion} · {timeLabel(item.validatedAt)}
                </summary>
                <Outcome data={item} replay />
              </details>
            ))}
          {replays.data?.items.length === 0 && <p>尚无离线重放记录。</p>}
        </section>
      )}
    </section>
  );
}

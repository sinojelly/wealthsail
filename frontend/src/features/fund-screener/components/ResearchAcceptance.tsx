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
const cellStyle = { padding: "8px", borderBottom: "1px solid var(--op-line)" };
const actionStyle = {
  border: "1px solid var(--op-line)",
  borderRadius: "6px",
  padding: "8px 12px",
  width: "fit-content",
  maxWidth: "100%",
};

export default function ResearchAcceptance({
  runId,
}: {
  runId?: string | null;
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
}: {
  runId: string;
  actor: string;
  queryKey: readonly (string | number)[];
}) {
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => api.getResearchAcceptance(runId, signal),
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  if (query.error instanceof HttpError && query.error.status === 404)
    return null;
  const data = query.data;
  if (!data || data.researchId !== runId || data.kind !== "INITIAL")
    return null;
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
      <Outcome data={data} />
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
      {observations("观察方向", accepted.valueDirections)}
      {accepted.industryCycleReviews.length > 0 && (
        <section aria-label="已验收行业核查">
          <h5>行业核查</h5>
          <ul>
            {accepted.industryCycleReviews.map((row, index) => (
              <li key={`${row.industry}:${index}`}>
                <strong>{row.industry}</strong>
                {row.evidenceSummary && <p>{row.evidenceSummary}</p>}
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

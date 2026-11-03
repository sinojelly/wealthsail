import { useContext, useRef, useState } from "react";
import {
  QueryClientContext,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import * as api from "../../../api/history-repairs";
import { getResearchAcceptance } from "../../../api/research-acceptance";
import {
  getSessionSubject,
  privateQueryKey,
  useAuthSession,
  type AuthSession,
} from "../../../app/auth-session";
import { sourceHref, timeLabel } from "../../fund-screener/macro-display";
import {
  AcceptanceIssues,
  AcceptanceObservations,
  RiskSummary,
} from "../../fund-screener/components/ResearchAcceptance";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const valid = (row: api.HistoricalRepair) =>
  row.kind === "HISTORICAL_REPAIR" &&
  row.publicationScope === "HISTORICAL_READ_ONLY" &&
  row.actionable === false;
const stateLabels = {
  PREVIEW: "条件预览",
  DRAFT: "待独立审核",
  APPROVED: "已审核，待显式发布",
  REJECTED: "已拒绝",
  PUBLISHED: "已发布",
};
const buttonStyle = {
  border: "1px solid var(--op-line)",
  borderRadius: 6,
  padding: "8px 12px",
  maxWidth: "100%",
};
const boxStyle = {
  border: "1px solid var(--op-line)",
  borderRadius: 8,
  padding: 16,
  marginTop: 12,
  overflowWrap: "anywhere" as const,
};

function QuarantineAnnex({ items }: { items: api.RepairQuarantine[] }) {
  if (!items.length) return null;
  return (
    <section aria-label="历史隔离说明">
      <h4>已隔离内容与原始依据</h4>
      {items.map((item, index) => (
        <article
          key={`${item.itemType}:${item.itemIndex ?? index}`}
          style={boxStyle}
        >
          <strong>
            {item.title ||
              `${item.itemType} ${item.itemIndex == null ? "" : item.itemIndex + 1}`}
          </strong>
          {item.importance && (
            <p>
              重要性：{{ HIGH: "高", MEDIUM: "中", LOW: "低" }[item.importance]}
            </p>
          )}
          {item.importance === "HIGH" && (
            <p role="status">
              <strong>高重要性 · 隔离材料须完整核对</strong>
            </p>
          )}
          {item.summary && <p>{item.summary}</p>}
          <p>隔离原因：{item.reason}</p>
          {!!item.catalysts?.length && (
            <div>
              <strong>原始催化线索 · 待核实</strong>
              <ul>
                {item.catalysts.map((catalyst, catalystIndex) => (
                  <li key={catalystIndex}>{catalyst.summary}</li>
                ))}
              </ul>
            </div>
          )}
          {item.industryCycle?.evidenceSummary && (
            <p>原始产业核查：{item.industryCycle.evidenceSummary}</p>
          )}
          <RiskSummary cycle={item.industryCycle} />
          {!!item.risks?.length && (
            <div>
              <strong>原始风险线索</strong>
              <ul>
                {item.risks.map((risk, riskIndex) => (
                  <li key={riskIndex}>{risk.summary}</li>
                ))}
              </ul>
            </div>
          )}
          {!!item.watchFor?.length && (
            <div>
              <strong>继续观察</strong>
              <ul>
                {item.watchFor.map((text, textIndex) => (
                  <li key={textIndex}>{text}</li>
                ))}
              </ul>
            </div>
          )}
          {!!item.unknowns?.length && (
            <div>
              <strong>尚未明确</strong>
              <ul>
                {item.unknowns.map((text, textIndex) => (
                  <li key={textIndex}>{text}</li>
                ))}
              </ul>
            </div>
          )}
          {!!item.channels?.length && (
            <div>
              <strong>原始传导假设</strong>
              <ul>
                {item.channels.map((text, textIndex) => (
                  <li key={textIndex}>{text}</li>
                ))}
              </ul>
            </div>
          )}
          {!!item.scenarios?.length && (
            <div>
              <strong>原始情景假设 · 未核实</strong>
              {item.scenarios.map((scenario, scenarioIndex) => (
                <div key={scenarioIndex}>
                  <p>条件：{scenario.condition}</p>
                  <p>影响：{scenario.impact}</p>
                  <p>反证：{scenario.counter}</p>
                </div>
              ))}
            </div>
          )}
          {item.sourceEvidence?.map((source) => {
            const href = sourceHref(source.url);
            return (
              <div key={source.id}>
                {href ? (
                  <a href={href} target="_blank" rel="noreferrer">
                    {source.title || source.id}
                  </a>
                ) : (
                  <span>{source.title || source.id}</span>
                )}
                <span>
                  {" "}
                  ·{" "}
                  {source.verification === "SOURCE_CHECKED"
                    ? "来源已核验"
                    : source.verification === "UNAVAILABLE"
                      ? "来源不可读取"
                      : "来源待核实"}
                </span>
                {source.quote && <blockquote>{source.quote}</blockquote>}
              </div>
            );
          })}
        </article>
      ))}
    </section>
  );
}

function RepairSummary({ data }: { data: api.HistoricalRepair }) {
  return (
    <article style={boxStyle} aria-label="历史研究只读摘要">
      <strong>历史只读研究 · 禁止用于实时买入</strong>
      {data.acceptedContentComplete === false && (
        <p role="status">
          <strong>验收正文为节选，完整内容请管理员查看冻结材料</strong>
        </p>
      )}
      <p>状态：{stateLabels[data.state]}</p>
      <p>原始研究：{data.sourceRunId} · 原始状态：FAILED</p>
      <p>
        原冻结证据时间：{timeLabel(data.validatedAt)} ·{" "}
        {data.currentFreshness === "EXPIRED" ? "旧信号已过期" : "仅供历史观察"}
      </p>
      <p>此独立历史版本不替换原报告，不产生跟踪、通知或交易动作。</p>
      <p>
        修复版本：{data.repairVersion}
        {data.repairRevision && (
          <> · 校验指纹 {data.repairRevision.slice(0, 12)}</>
        )}
        {data.buildCommit && <> · 构建 {data.buildCommit.slice(0, 12)}</>}
      </p>
      <AcceptanceObservations accepted={data.accepted} />
      <QuarantineAnnex items={data.quarantined ?? []} />
    </article>
  );
}

export function PublishedResearchRepairs() {
  const client = useContext(QueryClientContext);
  const session = useAuthSession();
  if (!client || !session) return null;
  return (
    <PublishedData
      key={`${getSessionSubject(session)}:${session.role}`}
      session={session}
    />
  );
}
function PublishedData({ session }: { session: AuthSession }) {
  const query = useQuery({
    queryKey: privateQueryKey(
      session,
      "research-history-repairs",
      session.role,
      "published",
    ),
    queryFn: ({ signal }) => api.listHistoricalRepairs(undefined, signal),
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const items =
    query.data?.items.filter(
      (row) => valid(row) && row.state === "PUBLISHED",
    ) ?? [];
  if (!items.length) return null;
  return (
    <section aria-label="已发布历史研究">
      <h3>已发布历史研究 · 只读</h3>
      {items.map((row) => (
        <RepairSummary key={row.id} data={row} />
      ))}
    </section>
  );
}

export default function ResearchHistoryRepairs({
  sourceRunId,
}: {
  sourceRunId?: string;
}) {
  const client = useContext(QueryClientContext);
  const session = useAuthSession();
  if (!client || !session || !["OWNER", "ADMIN"].includes(session.role))
    return null;
  return (
    <RepairManager
      key={`${getSessionSubject(session)}:${session.role}:${sourceRunId ?? ""}`}
      session={session}
      sourceRunId={sourceRunId}
    />
  );
}

type Command = {
  action: "GENERATE" | "APPROVE" | "REJECT" | "PUBLISH";
  target: string;
  source: string;
};
function RepairManager({
  session,
  sourceRunId,
}: {
  session: AuthSession;
  sourceRunId?: string;
}) {
  const client = useQueryClient();
  const [source, setSource] = useState(sourceRunId ?? "");
  const [listSource, setListSource] = useState(sourceRunId ?? "");
  const activeSource = useRef(source);
  const [previewTarget, setPreviewTarget] = useState<{
    source: string;
    nonce: number;
  } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [fullIssueSource, setFullIssueSource] = useState<string | null>(null);
  const [materialsId, setMaterialsId] = useState<string | null>(null);
  const keys = useRef(new Map<string, string>());
  const base = privateQueryKey(
    session,
    "research-history-repairs",
    session.role,
  );
  const list = useQuery({
    queryKey: [...base, "list", listSource],
    queryFn: ({ signal }) =>
      api.listHistoricalRepairs(listSource || undefined, signal),
    enabled: !listSource || uuid.test(listSource),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const preview = useQuery({
    queryKey: [...base, "preview", previewTarget?.source, previewTarget?.nonce],
    queryFn: ({ signal }) =>
      api.previewHistoricalRepair(previewTarget!.source, signal),
    enabled: !!previewTarget,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const detail = useQuery({
    queryKey: [...base, "detail", selectedId],
    queryFn: ({ signal }) => api.getHistoricalRepair(selectedId!, signal),
    enabled: !!selectedId,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const command = useMutation({
    retry: false,
    mutationFn: async (operation: Command) => {
      const slot = `wealthsail:history-repair:${getSessionSubject(session)}:${operation.action}:${operation.target}`;
      let key = keys.current.get(slot);
      if (!key) {
        try {
          const saved = sessionStorage.getItem(slot);
          if (saved && uuid.test(saved)) key = saved;
        } catch {
          /* Memory key still protects retries during this session. */
        }
        key ??= crypto.randomUUID();
        keys.current.set(slot, key);
        try {
          sessionStorage.setItem(slot, key);
        } catch {
          /* Storage may be unavailable. */
        }
      }
      const result =
        operation.action === "GENERATE"
          ? await api.generateHistoricalRepair(operation.target, key)
          : operation.action === "PUBLISH"
            ? await api.publishHistoricalRepair(operation.target, key)
            : await api.reviewHistoricalRepair(
                operation.target,
                key,
                operation.action,
              );
      if (
        !valid(result) ||
        result.sourceRunId !== operation.source ||
        !result.id ||
        (operation.action !== "GENERATE" && result.id !== operation.target)
      )
        throw new Error("Unexpected repair response");
      keys.current.delete(slot);
      try {
        if (sessionStorage.getItem(slot) === key)
          sessionStorage.removeItem(slot);
      } catch {
        /* No persistence is required after a confirmed response. */
      }
      return result;
    },
    onSuccess: (result, operation) => {
      client.setQueryData([...base, "detail", result.id], result);
      void client.invalidateQueries({ queryKey: [...base, "list"] });
      if (operation.action === "PUBLISH")
        void client.invalidateQueries({ queryKey: [...base, "published"] });
      if (activeSource.current === operation.source) {
        setSelectedId(result.id!);
        setPreviewTarget(null);
      }
    },
  });
  const candidate = selectedId ? detail.data : preview.data;
  const shown =
    candidate &&
    valid(candidate) &&
    candidate.sourceRunId === source &&
    (!selectedId || candidate.id === selectedId)
      ? candidate
      : undefined;
  const originalIssues = useQuery({
    queryKey: [...base, "original-issues", shown?.sourceRunId],
    queryFn: ({ signal }) => getResearchAcceptance(shown!.sourceRunId, signal),
    enabled:
      !!shown?.canInspectRepairDetails &&
      fullIssueSource === shown?.sourceRunId,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const materials = useQuery({
    queryKey: [...base, "materials", shown?.id],
    queryFn: ({ signal }) => api.getHistoricalRepairDetails(shown!.id!, signal),
    enabled:
      !!shown?.id &&
      !!shown.canInspectRepairDetails &&
      materialsId === shown.id,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  const fullMaterials =
    materialsId === shown?.id &&
    materials.data?.id === shown?.id &&
    materials.data?.sourceRunId === shown?.sourceRunId &&
    materials.data?.publicationScope === "HISTORICAL_READ_ONLY" &&
    materials.data?.actionable === false
      ? materials.data
      : undefined;
  const blocked =
    !shown?.strictPass ||
    shown.safetyBlocked ||
    !shown.historicalPublishAllowed ||
    !!shown.hardBlockers?.length;
  const canReview =
    shown?.canInspectRepairDetails &&
    shown.canReview &&
    shown.state === "DRAFT" &&
    shown.creatorId !== session.userId &&
    !blocked;
  const canReject =
    shown?.canInspectRepairDetails &&
    shown.canReject === true &&
    shown.state === "DRAFT" &&
    shown.creatorId !== session.userId;
  const canPublish =
    shown?.canInspectRepairDetails &&
    shown.canPublish &&
    shown.state === "APPROVED" &&
    shown.reviewerId &&
    shown.reviewerId !== shown.creatorId &&
    !blocked;
  const execute = (action: Command["action"]) => {
    if (shown)
      command.mutate({
        action,
        source,
        target: action === "GENERATE" ? source : shown.id!,
      });
  };
  const issues =
    fullIssueSource === shown?.sourceRunId &&
    originalIssues.data?.kind === "INITIAL" &&
    originalIssues.data.researchId === shown?.sourceRunId
      ? originalIssues.data
      : shown?.originalOutcome;
  const items = list.data?.items.filter(valid) ?? [];
  return (
    <section aria-label="历史研究修复管理" style={{ minWidth: 0 }}>
      <h2>历史研究修复</h2>
      <p>
        只处理已冻结的历史失败材料；条件预览不写入数据，生成、独立审核与历史发布均须分别确认。
      </p>
      {list.data?.canGenerate && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (uuid.test(source)) {
              setListSource(source);
              setSelectedId(null);
              setPreviewTarget((previous) => ({
                source,
                nonce: (previous?.nonce ?? 0) + 1,
              }));
              command.reset();
            }
          }}
        >
          <label>
            原始研究 ID
            <input
              aria-label="原始研究 ID"
              value={source}
              onChange={(event) => {
                const next = event.target.value.trim();
                activeSource.current = next;
                setSource(next);
                setSelectedId(null);
                setPreviewTarget(null);
                command.reset();
              }}
              style={{
                display: "block",
                width: "100%",
                maxWidth: 520,
                padding: 8,
                margin: "8px 0",
              }}
            />
          </label>
          <button
            type="submit"
            style={buttonStyle}
            disabled={!uuid.test(source) || preview.isFetching}
          >
            检查修复条件（只读）
          </button>
        </form>
      )}
      {list.isError && <p role="alert">历史修复列表暂不可读取。</p>}
      {items.length > 0 && (
        <div
          style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}
        >
          {items.map((row) => (
            <button
              key={row.id}
              style={buttonStyle}
              type="button"
              onClick={() => {
                activeSource.current = row.sourceRunId;
                setSource(row.sourceRunId);
                setListSource(row.sourceRunId);
                setSelectedId(row.id!);
                setPreviewTarget(null);
                command.reset();
              }}
            >
              查看修复版本 · {stateLabels[row.state]} · {row.id}
            </button>
          ))}
        </div>
      )}
      {(preview.isFetching || detail.isFetching) && (
        <p role="status">正在读取冻结校验结果…</p>
      )}
      {(preview.isError || detail.isError) && (
        <p role="alert">修复材料暂不可读取或不符合修复条件。</p>
      )}
      {command.isError && (
        <p role="alert">操作结果尚未确认，请重试同一操作。</p>
      )}
      {shown && (
        <>
          <RepairSummary data={shown} />
          {[
            ...(shown.blockers ?? []),
            ...(shown.autoBlockers ?? []),
            ...(shown.hardBlockers ?? []),
          ].map((blocker, index) => (
            <p role="status" key={`${blocker.code}:${index}`}>
              {blocker.reason}
            </p>
          ))}
          {shown.blockedReasons?.map((reason) => (
            <p key={reason}>{reason}</p>
          ))}
          {shown.state === "DRAFT" && shown.creatorId === session.userId && (
            <p>须由其他管理员独立审核。</p>
          )}
          <div
            style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}
          >
            {shown.state === "PREVIEW" && shown.canGenerate === true && (
              <button
                style={buttonStyle}
                type="button"
                disabled={command.isPending}
                onClick={() => execute("GENERATE")}
              >
                生成修复草稿
              </button>
            )}
            {canReview && (
              <button
                style={buttonStyle}
                type="button"
                disabled={command.isPending}
                onClick={() => execute("APPROVE")}
              >
                批准修复版本
              </button>
            )}
            {canReject && (
              <button
                style={buttonStyle}
                type="button"
                disabled={command.isPending}
                onClick={() => execute("REJECT")}
              >
                拒绝修复版本
              </button>
            )}
            {canPublish && (
              <button
                style={buttonStyle}
                type="button"
                disabled={command.isPending}
                onClick={() => execute("PUBLISH")}
              >
                发布历史只读摘要
              </button>
            )}
          </div>
          {shown.canInspectRepairDetails && (
            <details open style={boxStyle}>
              <summary>隔离差异、完整问题与版本依据</summary>
              <p>
                创建者：{shown.creatorId ?? "尚未创建"} · 独立审核者：
                {shown.reviewerId ?? "尚未审核"}
              </p>
              {shown.retained && (
                <>
                  <p>
                    保留方向：{shown.retained.directionKeys.join("；") || "无"}
                  </p>
                  <p>
                    保留组件计数：
                    {Object.entries(shown.retained.counts)
                      .map(
                        ([name, count]) =>
                          `${name}: ${typeof count === "number" ? count : typeof count === "object" && count && "acceptedCount" in count ? String(count.acceptedCount) : "—"}`,
                      )
                      .join("；")}
                  </p>
                </>
              )}
              {shown.quarantined?.map((item, index) => (
                <p key={index}>
                  {item.path ?? item.itemType} · {item.ruleId ?? "隔离"} ·{" "}
                  {item.reason}
                </p>
              ))}
              {Object.entries(shown.sourceDigests ?? {})
                .filter(([, hash]) => /^[0-9a-f]{64}$/i.test(hash))
                .map(([name, hash]) => (
                  <p key={name}>
                    冻结材料 {name} · SHA256 {hash}
                  </p>
                ))}
              {issues && (
                <AcceptanceIssues
                  data={{
                    researchId: shown.sourceRunId,
                    issues: issues.issues,
                    issueCount: issues.issueCount ?? 0,
                    issueListComplete: issues.issueListComplete ?? false,
                    canInspectArtifacts: true,
                    canReplay: false,
                  }}
                />
              )}
              <button
                type="button"
                style={buttonStyle}
                disabled={originalIssues.isFetching}
                onClick={() => {
                  setFullIssueSource(shown.sourceRunId);
                  if (fullIssueSource === shown.sourceRunId)
                    void originalIssues.refetch();
                }}
              >
                读取原始验收完整问题
              </button>
              {originalIssues.isError && (
                <p role="alert">原始验收详情暂不可读取。</p>
              )}
              {shown.id && (
                <p>
                  <button
                    type="button"
                    style={buttonStyle}
                    disabled={materials.isFetching}
                    onClick={() => {
                      setMaterialsId(shown.id!);
                      if (materialsId === shown.id) void materials.refetch();
                    }}
                  >
                    查看管理员完整修复材料
                  </button>
                </p>
              )}
              {materials.isError && (
                <p role="alert">完整修复材料暂不可读取。</p>
              )}
              {fullMaterials && (
                <section aria-label="完整历史材料">
                  <h4>管理员完整修复材料 · 历史只读</h4>
                  <p>历史评分仅供材料核对，禁止据此实时买入。</p>
                  <pre
                    aria-label="管理员完整修复材料"
                    style={{
                      maxHeight: 520,
                      overflow: "auto",
                      whiteSpace: "pre-wrap",
                      overflowWrap: "anywhere",
                      padding: 12,
                    }}
                  >
                    {JSON.stringify(fullMaterials, null, 2)}
                  </pre>
                </section>
              )}
            </details>
          )}
        </>
      )}
    </section>
  );
}

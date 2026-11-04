import { useContext, useRef, useState } from "react";
import { QueryClientContext, useQuery } from "@tanstack/react-query";
import {
  getResearchTopicRegistry,
  type RegistryTopic,
  type TopicRegistryPage,
  type TopicRegistryResult,
} from "../../../api/research-topic-registry";
import { HttpError } from "../../../api/client";
import {
  getSessionSubject,
  privateQueryKey,
  useAuthSession,
} from "../../../app/auth-session";
import { timeLabel } from "../macro-display";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function isPage(value: TopicRegistryResult): value is TopicRegistryPage {
  return (
    value.registryAvailable !== false &&
    "snapshotId" in value &&
    typeof value.snapshotId === "string" &&
    "revision" in value &&
    ((typeof value.revision === "string" &&
      /^[a-f0-9]{64}$/i.test(value.revision)) ||
      (value.revision === null && value.revisionStatus === "UNAVAILABLE")) &&
    "coverage" in value &&
    value.coverage != null &&
    Array.isArray(value.items)
  );
}
const statuses = {
  FORMAL: "严格候选已验收",
  OBSERVATION_ONLY: "仅供观察",
  PENDING_REVIEW: "待补证审核",
  QUARANTINED: "已隔离",
};
const matches = {
  STANDARD: "标准行业匹配",
  STRICT_ALIAS: "严格别名匹配",
  COMPOSITE: "复合主题",
  NEW_TOPIC: "新主题",
  UNMATCHED: "尚未匹配",
};
const kinds: Record<string, string> = {
  FINANCING_COOLING: "融资降温",
  LAYOFFS_CLOSURES: "欠薪裁员倒闭",
  ORDER_CUTS: "下游砍单",
  OVERCAPACITY_PRICE_WAR: "产能过剩与价格战",
  PRODUCTION_DELAY: "量产推迟",
};
const taskStatuses: Record<string, string> = {
  PLANNED: "计划核查",
  DEFERRED: "已延后，未完成核查",
  PENDING_REVIEW: "待审核",
  PLANNED_DEFERRED: "计划已延后，尚未执行核查",
};
const box = {
  border: "1px solid var(--op-line)",
  borderRadius: 8,
  padding: 14,
  marginTop: 12,
  overflowWrap: "anywhere" as const,
};
const button = {
  border: "1px solid var(--op-line)",
  borderRadius: 6,
  padding: "7px 10px",
};
type Props = {
  runId?: string | null;
  state?: string | null;
  completedAt?: string | null;
  variant?: "FORMAL" | "CURRENT";
};
export default function ResearchTopicRegistry({
  runId,
  state,
  completedAt,
  variant = "CURRENT",
}: Props) {
  const client = useContext(QueryClientContext);
  const session = useAuthSession();
  if (!client || !session || !runId || !uuid.test(runId)) return null;
  return (
    <RegistryData
      key={`${getSessionSubject(session)}:${session.role}:${runId}`}
      runId={runId}
      state={state}
      completedAt={completedAt}
      variant={variant}
      queryKey={privateQueryKey(
        session,
        "research-topic-registry",
        session.role,
        runId,
      )}
    />
  );
}
function RegistryData({
  runId,
  state,
  completedAt,
  variant,
  queryKey,
}: {
  runId: string;
  state?: string | null;
  completedAt?: string | null;
  variant: "FORMAL" | "CURRENT";
  queryKey: readonly (string | number)[];
}) {
  const [limit, setLimit] = useState<12 | 20 | 30>(12);
  const [cursors, setCursors] = useState<Array<string | undefined>>([
    undefined,
  ]);
  const [index, setIndex] = useState(0);
  const [snapshot, setSnapshot] = useState<string | null>(null);
  const reads = useRef({ lifecycle: "", count: 0 });
  const lifecycle = `${state ?? "UNKNOWN"}:${completedAt ?? "undated"}`;
  const terminal =
    state === "READY" ||
    state === "FAILED" ||
    (state === "UNCERTAIN" &&
      !!completedAt &&
      Number.isFinite(Date.parse(completedAt)));
  const cursor = cursors[index];
  const query = useQuery({
    queryKey: [...queryKey, lifecycle, limit, cursor ?? "first"],
    queryFn: ({ signal }) => {
      if (reads.current.lifecycle !== lifecycle)
        reads.current = { lifecycle, count: 0 };
      reads.current.count++;
      return getResearchTopicRegistry(runId, { limit, cursor }, signal);
    },
    enabled: terminal,
    retry: (count, error) =>
      count < 2 &&
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
      query.state.data?.registryAvailable === false &&
      query.state.data.archiveState !== "UNRECOVERABLE" &&
      reads.current.count < 30
        ? reads.current.count < 20
          ? 15_000
          : 60_000
        : false,
  });
  if (
    !terminal ||
    (query.error instanceof HttpError && query.error.status === 404)
  )
    return null;
  const data = query.data;
  if (!data && !query.isError) return null;
  const identity =
    data && data.researchId === runId && data.actionable === false;
  const available = identity && data && isPage(data) ? data : undefined;
  const sameSnapshot =
    available && (!snapshot || snapshot === available.snapshotId);
  const title = variant === "FORMAL" ? "正式报告主题目录" : "本次研究主题目录";
  const recheck = (
    <button
      type="button"
      style={button}
      disabled={query.isFetching}
      onClick={() => void query.refetch()}
    >
      重新检查主题目录
    </button>
  );
  return (
    <section
      id={`research-topic-registry-${runId}`}
      aria-label={title}
      style={box}
    >
      <h3>{title} · 只读观察</h3>
      <p>研究ID：{runId}</p>
      {variant === "CURRENT" && (
        <p>此区属于本次执行的观察记录，不替换当前展示的正式报告。</p>
      )}
      <p>
        行业匹配与正式资格不等于实时可交易；此目录不提供价格、跟踪或交易动作。
      </p>
      {query.isError && (
        <p role="alert">主题目录暂不可读取，可重新检查已保存记录。</p>
      )}
      {data && !identity && (
        <p role="alert">主题目录身份不一致，暂不展示内容。</p>
      )}
      {identity && data.registryAvailable !== false && !available && (
        <p role="alert">主题目录结构不完整，暂不展示内容。</p>
      )}
      {identity && data.registryAvailable === false && (
        <div role="status">
          <p>
            {data.archiveState === "UNRECOVERABLE"
              ? "主题目录无法自动恢复，请管理员核对冻结材料。"
              : "主题目录待归档，尚不能据此判断覆盖为零。"}
          </p>
          {data.reason && <p>{data.reason}</p>}
          {data.archiveState === "UNRECOVERABLE" && (
            <p>原始冻结材料与验收记录独立保留，可请管理员在验收区核对。</p>
          )}
        </div>
      )}
      {available && !sameSnapshot && (
        <div>
          <p role="alert">主题目录快照发生变化，请返回第一页核对。</p>
          <button
            type="button"
            style={button}
            onClick={() => {
              setCursors([undefined]);
              setIndex(0);
              setSnapshot(null);
            }}
          >
            返回主题第一页
          </button>
        </div>
      )}
      {sameSnapshot && (
        <>
          <p>
            证据冻结时间：{timeLabel(available.validatedAt)} · 快照：
            {available.snapshotId}
            {available.revision && (
              <> · 修订：{available.revision.slice(0, 12)}</>
            )}
          </p>
          {available.revision === null && <p>校验修订信息未提供</p>}
          <Coverage data={available} />
          <label>
            每页主题数{" "}
            <select
              aria-label="每页主题数"
              value={limit}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (value === 12 || value === 20 || value === 30) {
                  setLimit(value);
                  setCursors([undefined]);
                  setIndex(0);
                  setSnapshot(null);
                }
              }}
            >
              {[12, 20, 30].map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
          <p>
            共{available.totalCount}个主题 · 当前第{index + 1}页 · 本页
            {available.items.length}项
          </p>
          {available.items.map((topic) => (
            <Topic key={topic.topicId} topic={topic} />
          ))}
          {!available.items.length && <p>此快照没有可展示的主题。</p>}
          <nav
            aria-label="主题目录分页"
            style={{
              display: "flex",
              gap: 10,
              flexWrap: "wrap",
              marginTop: 12,
            }}
          >
            <button
              type="button"
              style={button}
              disabled={index === 0 || query.isFetching}
              onClick={() => setIndex((previous) => previous - 1)}
            >
              上一页主题
            </button>
            <button
              type="button"
              style={button}
              disabled={!available.nextCursor || query.isFetching}
              onClick={() => {
                if (available.nextCursor) {
                  setSnapshot(available.snapshotId);
                  setCursors((previous) => [
                    ...previous.slice(0, index + 1),
                    available.nextCursor!,
                  ]);
                  setIndex((previous) => previous + 1);
                }
              }}
            >
              下一页主题
            </button>
          </nav>
        </>
      )}
      {(query.isError ||
        !identity ||
        !available ||
        data?.registryAvailable === false) &&
        recheck}
    </section>
  );
}
function Coverage({ data }: { data: TopicRegistryPage }) {
  const coverage = data.coverage;
  const unavailable = "旧版未提供，不可推断";
  return (
    <div aria-label="主题登记与审核覆盖">
      <p>本批目录登记{coverage.totalCount}项（发现与正式方向按key汇总）</p>
      <p>
        计划审核
        {coverage.plannedScopeCount}个范围，审核结果记录
        {coverage.reviewedScopeCount}个范围，待审{coverage.pendingScopeCount}
        个范围。
      </p>
      <p>
        结构齐全范围：{coverage.structureCompleteScopeCount ?? unavailable}；
        执行凭证已核验范围：
        {coverage.executionVerifiedScopeCount ?? unavailable}
      </p>
      <p>
        执行凭证已核验检查：
        {coverage.executionVerifiedCheckCount ?? unavailable}； 证据已核验检查：
        {coverage.evidenceVerifiedCheckCount ?? unavailable}
      </p>
      <p>结构齐全不等于执行或证据核验；未核验不代表不存在风险。</p>
      <div aria-label="严格计算与自动发布许可">
        <p>
          {coverage.strictRevalidated === undefined ? (
            <>严格计算状态：{unavailable}</>
          ) : (
            <strong>
              {coverage.strictRevalidated
                ? "严格计算已通过"
                : "严格计算尚未通过"}
            </strong>
          )}
          {" · "}严格候选方向：{coverage.strictDirectionCount ?? unavailable}
        </p>
        <p>
          {coverage.autoPublishAllowed === undefined ? (
            <>自动发布状态：{unavailable}</>
          ) : (
            <strong>
              {coverage.autoPublishAllowed ? "允许自动发布" : "自动发布暂缓"}
            </strong>
          )}
          {" · "}发布阻断项：{coverage.autoPublishBlockerCount ?? unavailable}
        </p>
        {coverage.autoPublishAllowed === false && (
          <p>
            下一步：请管理员核对阻断原因，或在后续获授权批次补证；不会自动重跑研究。
          </p>
        )}
        {coverage.autoPublishAllowed === true && (
          <p>实际发布以该研究的正式报告状态为准，此目录仍为只读。</p>
        )}
      </div>
      {coverage.storedCount != null && (
        <p>
          目录已归档{coverage.storedCount}
          项，包含发现主题与正式方向按key去重后的记录。
        </p>
      )}
      {(coverage.deferredScopeCount ?? 0) > 0 && (
        <p>
          另有{coverage.deferredScopeCount}
          个范围延后，未完成核查不能作为正面结论。
        </p>
      )}
      <p>
        预算上限：每批{coverage.maxTopics ?? 64}个发现主题；正式方向每批最多
        {coverage.maxFormalDirections ?? 8}个；审核范围
        {coverage.maxScopes ?? 10}个、五类查询计划{coverage.maxQueries ?? 50}
        条、来源{coverage.maxSources ?? 24}项。
      </p>
      <p>独立搜索执行凭证尚未验证；五类核查结构不代表实际搜索完成。</p>
      <p>
        {coverage.maxModelCalls != null
          ? `模型调用预算上限：${coverage.maxModelCalls}次；`
          : "模型调用预算未提供；"}
        {coverage.additionalModelCalls != null &&
          `目录读取额外模型调用：${coverage.additionalModelCalls}次；`}
        实际用量以用量账本为准，翻页只读取保存记录，不调用AI。
      </p>
      {Object.entries(coverage.countsByStatus ?? {}).map(([status, count]) => (
        <span key={status} style={{ marginRight: 12 }}>
          {statuses[status as keyof typeof statuses] ?? "待核对"}：{count}
        </span>
      ))}
    </div>
  );
}
function Topic({ topic }: { topic: RegistryTopic }) {
  return (
    <article style={box}>
      <h4>{topic.title}</h4>
      <p>
        {topic.status === "FORMAL" && !topic.investmentEligible
          ? "严格候选已验收，未获得本批发布许可"
          : statuses[topic.status]}{" "}
        · {matches[topic.match.status]}
      </p>
      <p>{topic.summary}</p>
      <p>状态原因：{topic.reason}</p>
      <p>影响：{topic.impact}</p>
      {!!topic.measures.length && (
        <div>
          <strong>已采取措施</strong>
          <ul>
            {topic.measures.map((text, index) => (
              <li key={index}>{text}</li>
            ))}
          </ul>
        </div>
      )}
      <p>规范行业：{topic.match.industryIds.join("、") || "尚未核定"}</p>
      {!!topic.match.relatedIndustryIds.length && (
        <p>
          仅相关行业：{topic.match.relatedIndustryIds.join("、")}
          ；相关不等于完成同范围审核。
        </p>
      )}
      {!!topic.match.evidence.length && (
        <p>匹配依据：{topic.match.evidence.join("；")}</p>
      )}
      <p>匹配注册表：{topic.match.registryVersion}</p>
      <p>
        {topic.investmentEligible
          ? "正式资格已通过；不等于实时可交易，仍以正式计划与最新保护为准。"
          : "投资许可：仅供观察，尚未获正式投资资格。"}
      </p>
      {!!topic.pendingTasks.length && (
        <div>
          <h5>待补证任务 · 未自动执行</h5>
          <ul>
            {topic.pendingTasks.map((task) => (
              <li key={task.taskId}>
                <strong>
                  {kinds[task.kind] ?? task.kind} ·{" "}
                  {taskStatuses[task.status] ?? "待核对"}
                </strong>
                <p>{task.query}</p>
                {task.window && (
                  <p>
                    原核查窗口：{task.window.start} 至 {task.window.end}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      <p>
        同次研究的冻结来源引用：{topic.sourceIds.join("、") || "尚无已关联来源"}
      </p>
    </article>
  );
}

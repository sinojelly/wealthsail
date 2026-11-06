import { useContext } from "react";
import { QueryClientContext, useQuery } from "@tanstack/react-query";
import { HttpError } from "../../../api/client";
import {
  getTopicFundRecommendations,
  type TopicFundRecommendation,
  type TopicRecommendedFund,
} from "../../../api/topic-fund-recommendations";
import { privateQueryKey, useAuthSession } from "../../../app/auth-session";
import { timeLabel } from "../macro-display";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const box = {
  border: "1px solid var(--op-line)",
  borderRadius: 10,
  padding: 14,
  marginTop: 12,
  overflowWrap: "anywhere" as const,
};
const riskLabels = {
  VERIFIED_SAFE: "风险核查已完成",
  RISK_ALERT: "存在负面分歧",
  UNVERIFIED_RISK: "负面风险待核验",
};
const matchLabels = {
  CORE_MATCH: "精选池匹配",
  DYNAMIC_MATCH: "全量库动态匹配",
  BROAD_FALLBACK: "宽基兜底匹配",
};

function Fund({ fund }: { fund: TopicRecommendedFund }) {
  const subscription = fund.subscription;
  const subscriptionLabel =
    !subscription || subscription.status === "UNKNOWN"
      ? "申购状态待核验"
      : subscription.status === "LIMITED"
        ? "申购限额，需核对销售渠道"
        : subscription?.status === "SUSPENDED" ||
            subscription?.status === "PAUSED"
          ? "暂停申购"
          : subscription?.restricted === true
            ? "申购受限，需核对销售渠道"
            : subscription?.restricted === false &&
                subscription.status === "OPEN"
              ? "资料记录可申购，仍需核对最新状态"
              : "申购状态待核验";
  return (
    <li>
      <strong>
        {fund.fundCode} · {fund.name}
      </strong>
      <p>
        <span>{matchLabels[fund.matchLevel] ?? "匹配等级待核验"}</span> ·{" "}
        {subscriptionLabel}
      </p>
      <p>匹配依据：{fund.matchReason || "关联依据待补充"}</p>
      <p>
        量化得分：
        {typeof fund.quantitativeScore === "number" &&
        Number.isFinite(fund.quantitativeScore)
          ? fund.quantitativeScore
          : "资料不足，尚未评分"}
        {typeof fund.quantitativeScore === "number" &&
          Number.isFinite(fund.quantitativeScore) &&
          typeof fund.unadjustedQuantitativeScore === "number" &&
          Number.isFinite(fund.unadjustedQuantitativeScore) &&
          `（扣分前${fund.unadjustedQuantitativeScore}分）`}
      </p>
      {!!fund.missingEvidence?.length && (
        <p>基金资料待补齐，请核对最新净值、历史价格与申购限制。</p>
      )}
      {!!fund.riskWarnings?.length && (
        <ul aria-label="基金风险提示">
          {fund.riskWarnings.map((warning, index) => (
            <li key={index}>{warning}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

function Topic({ topic }: { topic: TopicFundRecommendation }) {
  return (
    <article style={box}>
      <h4>{topic.title}</h4>
      <p>{topic.thesis}</p>
      <p>
        <strong
          style={{
            color:
              topic.riskStatus === "RISK_ALERT"
                ? "var(--op-warning, #9a6700)"
                : undefined,
          }}
        >
          {riskLabels[topic.riskStatus] ?? riskLabels.UNVERIFIED_RISK}
        </strong>
      </p>
      {Number.isFinite(topic.riskScorePenalty) &&
        topic.riskScorePenalty > 0 && (
          <p>负面风险扣分：-{topic.riskScorePenalty}</p>
        )}
      {!!topic.riskWarnings?.length && (
        <ul aria-label="风险提示">
          {topic.riskWarnings.map((warning, index) => (
            <li key={index}>{warning}</li>
          ))}
        </ul>
      )}
      {topic.recommendationStatus === "SOURCE_PENDING" &&
        topic.recommendedFunds.length > 0 && (
          <p>正面来源待核验，以下基金仅为主题匹配候选。</p>
        )}
      {topic.recommendedFunds.length > 0 ? (
        <ul aria-label={`${topic.title}的匹配基金`}>
          {topic.recommendedFunds.map((fund) => (
            <Fund key={fund.fundCode} fund={fund} />
          ))}
        </ul>
      ) : (
        <p>
          {topic.recommendationStatus === "SOURCE_PENDING"
            ? "正面来源待核验，基金匹配仍待补充。"
            : "当前没有满足匹配条件的基金，机会逻辑与待补资料继续保留。"}
        </p>
      )}
      {topic.matchMessage && <p>基金匹配说明：{topic.matchMessage}</p>}
      {!!topic.missingEvidence?.length && (
        <p>此机会仍有待补证资料，风险未核验不代表不存在风险。</p>
      )}
      <p>同次研究来源：{topic.sourceIds.join("、") || "尚未提供"}</p>
    </article>
  );
}

export default function TopicFundRecommendations({
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
  if (!client || !session || !runId || !uuid.test(runId)) return null;
  const terminal =
    state === "READY" ||
    state === "FAILED" ||
    (state === "UNCERTAIN" &&
      !!completedAt &&
      Number.isFinite(Date.parse(completedAt)));
  if (!terminal) return null;
  return (
    <RecommendationsData
      key={`${session.tenantId}:${session.userId}:${session.role}:${runId}`}
      runId={runId}
      lifecycle={`${state}:${completedAt ?? "undated"}`}
      queryKey={privateQueryKey(
        session,
        "topic-fund-recommendations",
        session.role,
        runId,
      )}
    />
  );
}

function RecommendationsData({
  runId,
  lifecycle,
  queryKey,
}: {
  runId: string;
  lifecycle: string;
  queryKey: readonly (string | number)[];
}) {
  const query = useQuery({
    queryKey: [...queryKey, lifecycle],
    queryFn: ({ signal }) => getTopicFundRecommendations(runId, signal),
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  if (query.error instanceof HttpError && query.error.status === 404)
    return null;
  const data = query.data;
  const valid =
    data &&
    data.researchId === runId &&
    data.actionable === false &&
    Array.isArray(data.items) &&
    data.coverage;
  return (
    <section aria-label="新闻机会与匹配基金" style={box}>
      <h3>新闻机会与匹配基金</h3>
      <p>
        由本次研究主题匹配基金；风险核查结果与基金匹配并列展示。匹配不等于实时可交易，仍需核对最新价格和申购条件。
      </p>
      {query.isPending && <p role="status">正在读取机会与基金匹配…</p>}
      {query.isError && (
        <p role="alert">机会基金匹配暂不可读取，请稍后重新检查。</p>
      )}
      {data && !valid && (
        <p role="alert">机会基金匹配身份或结构不完整，暂不展示内容。</p>
      )}
      {valid && (
        <>
          <p>
            研究ID：{data.researchId} · 证据时间：{timeLabel(data.validatedAt)}{" "}
            · 匹配时间：{timeLabel(data.evaluatedAt)}
          </p>
          {data.status === "UNAVAILABLE" ? (
            <p>本次机会基金匹配资料尚不完整，不能据此判断没有机会或基金。</p>
          ) : (
            <p>
              机会主题{data.coverage.topicCount}个 · 已匹配
              {data.coverage.matchedTopicCount}个 · 匹配基金
              {data.coverage.recommendedFundCount}只
            </p>
          )}
          {data.status === "PARTIAL" && (
            <p>部分来源或匹配资料尚待补齐，已匹配的基金继续展示。</p>
          )}
          <p>
            优先展示已匹配基金的主题，再依次比较来源支持、量化得分与风险核查完成度。
          </p>
          {data.items.slice(0, 10).map((topic) => (
            <Topic key={topic.topicId} topic={topic} />
          ))}
          {data.items.length > 10 && (
            <details>
              <summary>
                查看其余{data.items.length - 10}个机会主题与匹配基金
              </summary>
              {data.items.slice(10).map((topic) => (
                <Topic key={topic.topicId} topic={topic} />
              ))}
            </details>
          )}
          {!data.items.length && data.status !== "UNAVAILABLE" && (
            <p>本次尚无可展示的机会主题，研究主题目录仍可阅读。</p>
          )}
        </>
      )}
      {(query.isError || (valid && data.status === "UNAVAILABLE")) && (
        <button
          type="button"
          disabled={query.isFetching}
          onClick={() => void query.refetch()}
        >
          重新检查机会基金匹配
        </button>
      )}
    </section>
  );
}

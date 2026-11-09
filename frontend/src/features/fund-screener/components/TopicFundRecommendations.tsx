import { useContext, useEffect, useRef, useState } from "react";
import { QueryClientContext, useQuery } from "@tanstack/react-query";
import { HttpError } from "../../../api/client";
import {
  getTopicFundRecommendations,
  type TopicFundRecommendation,
  type TopicRecommendedFund,
  type TopicFundRecommendationsPage,
} from "../../../api/topic-fund-recommendations";
import { privateQueryKey, useAuthSession } from "../../../app/auth-session";
import { timeLabel } from "../macro-display";
import ObservationSection from "./ObservationSection";
import ViewportFundChart from "./ViewportFundChart";
import { readingText, researchSummary } from "../research-reading";
import "../topic-fund-cards.css";
import QuantitativePricePanel from "./QuantitativePricePanel";
import { referenceTrack, quantitativePosition } from "../quantitative-position";
import { planPrice } from "../plan-display";
import { planNavSnapshot } from "../plan-nav-snapshot";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const riskLabels = {
  VERIFIED_SAFE: "风险核查已完成",
  RISK_ALERT: "存在负面分歧",
  UNVERIFIED_RISK: "负面风险待核验",
};
const matchLabels = {
  CORE_MATCH: "精选池匹配",
  DYNAMIC_MATCH: "全量库动态匹配",
  BROAD_FALLBACK: "宽基间接观察",
};
const associationLabels = {
  DIRECT_INDEX: "指数直配",
  CATEGORY_MATCH: "分类范围匹配",
  NAME_CANDIDATE: "名称匹配候选",
  BROAD_FALLBACK: "宽基间接观察",
};
const polarityLabels = {
  POSITIVE_OPPORTUNITY: "正向机会线索",
  NEGATIVE_RISK: "负面风险线索",
  MIXED: "多空因素并存",
  MACRO_OBSERVATION: "宏观观察",
  UNDETERMINED: "方向待核实",
};
const importanceLabels = {
  HIGH: "高重要性事件关联",
  MEDIUM: "中重要性事件关联",
  LOW: "低重要性事件关联",
  UNKNOWN: "事件重要性未提供",
};
function isIndirect(fund: TopicRecommendedFund) {
  return (
    fund.associationLevel === "BROAD_FALLBACK" ||
    fund.matchLevel === "BROAD_FALLBACK"
  );
}
function isMatchingPending(data?: TopicFundRecommendationsPage) {
  return (
    data?.archiveState !== "UNRECOVERABLE" &&
    (data?.missingEvidence?.some(
      (code) =>
        code === "TOPIC_ARCHIVE_PENDING" || code === "FUND_RECALL_PENDING",
    ) ||
      data?.archiveState === "TOPIC_ARCHIVE_PENDING" ||
      data?.archiveState === "FUND_RECALL_PENDING" ||
      data?.archiveState === "PENDING_ARCHIVE" ||
      data?.archiveState === "AUDIT_PENDING" ||
      data?.archiveState === "ARCHIVING")
  );
}

function subscriptionLabel(fund: TopicRecommendedFund): string {
  const subscription = fund.subscription;
  return !subscription || subscription.status === "UNKNOWN"
    ? "申购状态待核验"
    : subscription.status === "LIMITED"
      ? "申购限额，需核对销售渠道"
      : subscription?.status === "SUSPENDED" ||
          subscription?.status === "PAUSED"
        ? "暂停申购"
        : subscription?.restricted === true
          ? "申购受限，需核对销售渠道"
          : subscription?.restricted === false && subscription.status === "OPEN"
            ? "资料记录可申购，仍需核对最新状态"
            : "申购状态待核验";
}

function FundPrices({ fund }: { fund: TopicRecommendedFund }) {
  const plan = fund.actionableZones;
  const nav = fund.confirmedNav?.value ?? plan?.currentPrice;
  const change = fund.confirmedNav?.changePct ?? plan?.currentChangePct;
  return (
    <>
      <p>
        最新净值：
        <strong>
          {planPrice(nav, { subjectKind: "nav", priceBasis: "UNIT_NAV" })}
        </strong>
        {change != null && Number.isFinite(Number(change)) && (
          <span className="op-nav-change">
            {" "}
            · {Number(change) > 0 ? "+" : ""}
            {Number(change).toFixed(2)}%
          </span>
        )}
        {fund.confirmedNav?.asOf || plan?.asOf
          ? ` · ${timeLabel(fund.confirmedNav?.asOf ?? plan?.asOf)}`
          : ""}
      </p>
      {plan ? (
        <QuantitativePricePanel plan={plan} />
      ) : (
        <small>参考买卖点正在补齐净值资料。</small>
      )}
    </>
  );
}

function Fund({ fund }: { fund: TopicRecommendedFund }) {
  const snapshot =
    fund.history ??
    (fund.actionableZones
      ? planNavSnapshot(fund.actionableZones, fund.name)
      : undefined);
  return (
    <li className="op-match-fund">
      <header>
        <strong>
          {fund.fundCode} · {fund.name}
        </strong>
        <span className="op-match-badge">
          {fund.associationLevel
            ? associationLabels[fund.associationLevel]
            : (matchLabels[fund.matchLevel] ?? "匹配等级待核验")}
        </span>
      </header>
      <div className="op-match-fund-layout">
        <div className="op-match-fund-info">
          <p className="op-match-reason">
            匹配依据：
            {fund.associationReason || fund.matchReason || "关联依据待补充"}
          </p>
          <div className="op-match-fund-metrics">
            <p>
              <span>申购状态</span>
              <strong>{subscriptionLabel(fund)}</strong>
            </p>
            <p>
              <span>量化评分</span>
              <strong>
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
              </strong>
            </p>
            {fund.pricePosition?.actionable === false && (
              <p>
                <span>价格观察</span>
                <strong>技术位置：{fund.pricePosition.label}</strong>
                {fund.pricePosition.asOf && (
                  <small>数据时间：{timeLabel(fund.pricePosition.asOf)}</small>
                )}
              </p>
            )}
            {fund.salesChannel && (
              <p>
                <span>交易渠道</span>
                <strong>
                  销售场景：
                  {fund.salesChannel === "OFF_EXCHANGE"
                    ? "场外基金"
                    : fund.salesChannel === "EXCHANGE"
                      ? "场内ETF，需证券交易渠道"
                      : "渠道待核实"}
                </strong>
              </p>
            )}
          </div>
          <FundPrices fund={fund} />
          {!!fund.missingEvidence?.length && (
            <p className="op-match-warning">
              基金资料待补齐，请核对最新净值、历史价格与申购限制。
            </p>
          )}
          {!!fund.riskWarnings?.length && (
            <p className="op-match-warning">
              另有{fund.riskWarnings.length}项基金风险提示，请展开核对。
            </p>
          )}
          <details className="op-match-details">
            <summary>基金资料与风险详情</summary>
            {!!fund.alternativeShareClasses?.length && (
              <p>
                其他份额：
                {fund.alternativeShareClasses
                  .map((share) => `${share.fundCode} · ${share.name}`)
                  .join("；")}
              </p>
            )}
            {fund.pricePosition?.actionable === false && (
              <div>
                {!!fund.pricePosition.reasons.length && (
                  <ul aria-label="技术位置依据">
                    {fund.pricePosition.reasons.map((reason, index) => (
                      <li key={index}>
                        {reason === "OUTSIDE_TECHNICAL_ENTRY_ZONE"
                          ? "当前净值尚未进入技术观察区间"
                          : readingText(reason, Infinity)}
                      </li>
                    ))}
                  </ul>
                )}
                <p>技术位置仅供观察，不代表估值结论或买入许可。</p>
              </div>
            )}
            {!!fund.riskWarnings?.length && (
              <ul aria-label="基金风险提示">
                {fund.riskWarnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            )}
          </details>
        </div>
        {/^[0-9]{6}$/.test(fund.fundCode) && (
          <ViewportFundChart
            fundCode={fund.fundCode}
            track={
              fund.actionableZones
                ? referenceTrack(fund.actionableZones)
                : undefined
            }
            confirmedNavAsOf={
              fund.confirmedNav?.asOf ?? fund.actionableZones?.asOf ?? undefined
            }
            {...(snapshot
              ? {
                  snapshot,
                  snapshotMode: "CURRENT_LOCAL" as const,
                }
              : {})}
          />
        )}
      </div>
    </li>
  );
}

function topicFundGroups(topic: TopicFundRecommendation) {
  const direct = topic.recommendedFunds.filter((fund) => !isIndirect(fund));
  const negative = topic.topicPolarity === "NEGATIVE_RISK";
  const observation =
    topic.topicPolarity === "MACRO_OBSERVATION" ||
    topic.topicPolarity === "UNDETERMINED";
  const recommended = negative || observation ? [] : direct;
  const riskFunds = [
    ...(topic.relatedRiskFunds ?? []),
    ...(negative ? direct : []),
  ];
  const observationFunds = [
    ...(topic.observationFunds ?? []),
    ...(observation ? direct : []),
  ];
  const indirect = [
    ...(topic.indirectFunds ?? []),
    ...topic.recommendedFunds.filter(isIndirect),
  ];
  const groups = [
    { title: "主题匹配候选", label: "匹配基金", funds: recommended },
    { title: "风险涉及基金", label: "风险涉及基金", funds: riskFunds },
    { title: "相关观察基金", label: "相关观察基金", funds: observationFunds },
    { title: "间接敞口观察", label: "间接观察基金", funds: indirect },
  ];
  return { groups, recommended, negative };
}

function Topic({ topic }: { topic: TopicFundRecommendation }) {
  const thesisPreview = researchSummary(topic.thesis, 240);
  const { groups, negative } = topicFundGroups(topic);
  return (
    <article className="op-match-topic">
      <div className="op-match-thesis">
        <header>
          <h4>{topic.title}</h4>
          {topic.topicPolarity && topic.topicPolarity !== "UNDETERMINED" && (
            <span className="op-match-badge">
              {polarityLabels[topic.topicPolarity]}
            </span>
          )}
        </header>
        <p>{thesisPreview}</p>
        {topic.priority && (
          <p className="op-match-note">
            {importanceLabels[topic.priority.importance ?? "UNKNOWN"]}
          </p>
        )}
        {topic.riskStatus === "RISK_ALERT" &&
          topic.riskWarnings?.length > 0 && (
            <p className="op-match-warning">
              ⚠️ 行业反方风险提示：{topic.riskWarnings[0]}
            </p>
          )}
        <details className="op-match-details">
          <summary>研究依据与核查详情</summary>
          <p>{riskLabels[topic.riskStatus] ?? riskLabels.UNVERIFIED_RISK}</p>
          {topic.riskScorePenalty > 0 && (
            <p>负面风险扣分：-{topic.riskScorePenalty}</p>
          )}
          {topic.recommendationStatus === "SOURCE_PENDING" && (
            <p>正面来源待核验，以下基金仅为主题匹配候选。</p>
          )}
          {topic.riskWarnings?.map((warning, index) => (
            <p key={index}>{warning}</p>
          ))}
          {thesisPreview !== topic.thesis && <p>{topic.thesis}</p>}
          {topic.priority && (
            <p>
              {topic.priority.checkedSourceCoveragePercent == null
                ? "引文核对覆盖未知"
                : `引文核对覆盖${topic.priority.checkedSourceCoveragePercent}%（不代表主题推理已证实）`}
              {topic.priority.scopeVerified
                ? " · 行业范围关联已核实"
                : " · 行业范围关联待核实"}
            </p>
          )}
          {!!topic.polarityReasons?.length && (
            <ul aria-label="主题方向依据">
              {topic.polarityReasons.map((reason, index) => (
                <li key={index}>{reason}</li>
              ))}
            </ul>
          )}
          {!!topic.missingEvidence?.length && (
            <p>此机会仍有待补证资料，风险未核验不代表不存在风险。</p>
          )}
          <p>同次研究来源：{topic.sourceIds.join("、") || "尚未提供"}</p>
        </details>
        {negative && (
          <p className="op-match-warning">
            仅提示相关行业风险，风险涉及基金不作为推荐。
          </p>
        )}
      </div>
      <div className="op-match-funds">
        {groups.map(
          ({ title, label, funds }) =>
            funds.length > 0 && (
              <div className="op-match-group" key={label}>
                <h5>{title}</h5>
                <ul aria-label={`${topic.title}的${label}`}>
                  {funds.map((fund) => (
                    <Fund key={fund.fundCode} fund={fund} />
                  ))}
                </ul>
              </div>
            ),
        )}
        {!groups.some((group) => group.funds.length > 0) && (
          <p>
            {topic.recommendationStatus === "SOURCE_PENDING"
              ? "正面来源待核验，基金匹配仍待补充。"
              : topic.missingEvidence?.length
                ? "基金匹配资料待补齐，不能据此判断没有基金机会。"
                : topic.recommendationStatus === "NO_MATCH"
                  ? "当前没有满足匹配条件的基金，机会逻辑与待补资料继续保留。"
                  : "此主题尚无已归档的基金匹配条目，不能据此判断没有基金机会。"}
          </p>
        )}
        {topic.matchMessage && <p>基金匹配说明：{topic.matchMessage}</p>}
      </div>
    </article>
  );
}

function TopicSummary({ topic }: { topic: TopicFundRecommendation }) {
  const [expanded, setExpanded] = useState(false);
  const { groups } = topicFundGroups(topic);
  const funds = [
    ...new Map(
      groups.flatMap((group) =>
        group.funds.map(
          (fund) => [fund.fundCode, { fund, title: group.title }] as const,
        ),
      ),
    ).values(),
  ];
  return (
    <article className="op-opportunity-summary">
      <header>
        <h4>{topic.title}</h4>
      </header>
      <p>{researchSummary(topic.thesis, 150)}</p>
      {topic.riskStatus === "RISK_ALERT" && topic.riskWarnings?.length > 0 && (
        <p className="op-match-warning">
          ⚠️ 行业反方风险提示：{topic.riskWarnings[0]}
        </p>
      )}
      {funds.length ? (
        <ul
          className="op-summary-funds"
          aria-label={`${topic.title}的基金摘要`}
        >
          {funds.map(({ fund, title }) => (
            <li key={fund.fundCode}>
              <strong>
                {fund.name} {fund.fundCode}
              </strong>
              <span>
                {title} ·{" "}
                {fund.associationLevel
                  ? associationLabels[fund.associationLevel]
                  : (matchLabels[fund.matchLevel] ?? "主题关联")}{" "}
                · {subscriptionLabel(fund)}
              </span>
              {!fund.actionableZones && fund.pricePosition && (
                <span>
                  价格观察：{fund.pricePosition.label} ·{" "}
                  {timeLabel(fund.pricePosition.asOf)}
                </span>
              )}
              <FundPrices fund={fund} />
            </li>
          ))}
        </ul>
      ) : (
        <p>基金匹配资料正在补齐。</p>
      )}
      <details onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary>查看{topic.title}的基金与走势图</summary>
        {expanded && <Topic topic={topic} />}
      </details>
    </article>
  );
}

function topicPriceOrder(topic: TopicFundRecommendation): [number, number] {
  const funds = topicFundGroups(topic).groups.flatMap((group) => group.funds);
  const ranks = funds.map((fund) =>
    fund.actionableZones ? quantitativePosition(fund.actionableZones).rank : 2,
  );
  return [
    ranks.length ? Math.min(...ranks) : 4,
    Math.max(0, ...funds.map((fund) => fund.quantitativeScore ?? 0)),
  ];
}

export default function TopicFundRecommendations({
  runId,
  state,
  completedAt,
  source = "REPORT",
  historical = false,
  defaultExpanded = true,
  presentation = "FULL",
}: {
  runId?: string | null;
  state?: string | null;
  completedAt?: string | null;
  source?: "REPORT" | "CURRENT";
  historical?: boolean;
  defaultExpanded?: boolean;
  presentation?: "FULL" | "SUMMARY";
}) {
  const client = useContext(QueryClientContext);
  const session = useAuthSession();
  const hasCompletion =
    !!completedAt && Number.isFinite(Date.parse(completedAt));
  const completedStaleResearch =
    source === "CURRENT" && state === "STALE" && hasCompletion;
  const terminal =
    state === "READY" ||
    state === "FAILED" ||
    (state === "UNCERTAIN" && hasCompletion) ||
    completedStaleResearch;
  const title = source === "CURRENT" ? "本次研究基金观察" : "高价值机会";
  return (
    <ObservationSection
      title={title}
      className="op-topic-recommendations"
      defaultExpanded={defaultExpanded}
    >
      <div className="op-match-source">
        <span className="op-match-badge">
          {source === "CURRENT"
            ? "本次研究 · 只读观察线索"
            : historical
              ? "最近有效报告 · 历史 · 只读"
              : "展示报告 · 只读"}
        </span>
        <span>
          来源时间：{completedAt ? timeLabel(completedAt) : "尚未确定"}
        </span>
      </div>
      <p className="op-match-note">
        主题匹配仅供研究观察，买卖条件需独立核验。
      </p>
      <details className="op-match-details">
        <summary>报告来源与匹配说明</summary>
        <p>来源研究ID：{runId && uuid.test(runId) ? runId : "尚未确定"}</p>
        <p>
          由对应研究主题匹配基金；风险核查结果与基金匹配并列展示。匹配不等于实时可交易，仍需核对最新价格和申购条件。
        </p>
      </details>
      {completedStaleResearch && (
        <p>最近一次研究已结束；今日尚未更新，仅显示该次研究的只读归档状态。</p>
      )}
      {!client || !session ? (
        <p role="status">请先登录以读取当前账户的基金匹配资料。</p>
      ) : !runId || !uuid.test(runId) ? (
        <p role="status">基金匹配来源尚未确定，等待展示报告或研究记录。</p>
      ) : (
        <RecommendationsData
          key={`${session.tenantId}:${session.userId}:${session.role}:${runId}`}
          runId={runId}
          terminal={terminal}
          presentation={presentation}
          lifecycle={`${state}:${completedAt ?? "undated"}`}
          queryKey={privateQueryKey(
            session,
            "topic-fund-recommendations",
            session.role,
            runId,
          )}
        />
      )}
    </ObservationSection>
  );
}

function RecommendationsData({
  runId,
  lifecycle,
  queryKey,
  terminal,
  presentation,
}: {
  runId: string;
  lifecycle: string;
  queryKey: readonly (string | number)[];
  terminal: boolean;
  presentation: "FULL" | "SUMMARY";
}) {
  const reads = useRef(0);
  const previousLifecycle = useRef(lifecycle);
  const [exhaustedLifecycle, setExhaustedLifecycle] = useState<string | null>(
    null,
  );
  const query = useQuery({
    queryKey,
    enabled: terminal,
    queryFn: async ({ signal }) => {
      reads.current++;
      const data = await getTopicFundRecommendations(runId, signal);
      if (reads.current > 10) setExhaustedLifecycle(lifecycle);
      return data;
    },
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: (query) =>
      isMatchingPending(query.state.data) ? "always" : true,
    refetchInterval: (query) => {
      const data = query.state.data;
      return !query.state.error &&
        data?.researchId === runId &&
        data.actionable === false &&
        Array.isArray(data.items) &&
        data.coverage &&
        reads.current <= 10 &&
        isMatchingPending(data)
        ? 3000
        : false;
    },
  });
  const { refetch } = query;
  useEffect(() => {
    if (previousLifecycle.current === lifecycle) return;
    previousLifecycle.current = lifecycle;
    reads.current = 0;
    if (terminal) void refetch({ cancelRefetch: false });
  }, [lifecycle, terminal, refetch]);
  const data = query.data;
  const valid =
    data &&
    data.researchId === runId &&
    data.actionable === false &&
    Array.isArray(data.items) &&
    data.coverage;
  const pricesPending =
    valid &&
    data.items.some((topic) =>
      [
        topic.recommendedFunds,
        topic.relatedRiskFunds,
        topic.observationFunds,
        topic.indirectFunds,
      ].some((funds) =>
        funds?.some((fund) => fund.pricePosition?.status === "DATA_PENDING"),
      ),
    );
  const confirmedZero =
    valid &&
    data.status === "AVAILABLE" &&
    data.archiveState === "ARCHIVED" &&
    !isMatchingPending(data) &&
    !data.missingEvidence?.length &&
    data.coverage.matchedTopicCount === 0 &&
    data.coverage.recommendedFundCount === 0;
  return (
    <>
      {!terminal && (
        <p role="status">研究尚未完成，基金匹配等待研究结果归档。</p>
      )}
      {terminal && query.isPending && (
        <p role="status">正在读取机会与基金匹配…</p>
      )}
      {query.isFetching && valid && (
        <p role="status">正在刷新基金匹配，继续显示此前已读取的资料。</p>
      )}
      {query.isError && (
        <>
          <p role="alert">
            {query.error instanceof HttpError && query.error.status === 404
              ? "未找到该研究的基金匹配归档记录；缺少记录不代表没有基金机会。"
              : "机会基金匹配暂不可读取，请稍后重新检查。"}
          </p>
          {valid && (
            <p role="status">刷新未完成，继续显示此前已读取的匹配资料。</p>
          )}
        </>
      )}
      {data && !valid && (
        <p role="alert">机会基金匹配身份或结构不完整，暂不展示内容。</p>
      )}
      {valid && (
        <>
          <details className="op-match-details op-match-archive">
            <summary>研究来源与数据时间</summary>
            <p>
              研究ID：{data.researchId} · 证据时间：
              {timeLabel(data.validatedAt)} · 匹配时间：
              {timeLabel(data.evaluatedAt)}
            </p>
            <p>
              优先展示具备直接基金匹配的主题，结合事件重要性、来源支持、关联程度与风险核查完成度排序；重点展示不增加投资资格。
            </p>
          </details>
          {data.status === "UNAVAILABLE" ? (
            <p>本次机会基金匹配资料尚不完整，不能据此判断没有机会或基金。</p>
          ) : (
            <p className="op-match-coverage">
              研究主题{data.coverage.topicCount}个 · 直接匹配
              {data.coverage.matchedTopicCount}个 · 直接候选基金
              {data.coverage.recommendedFundCount}只
            </p>
          )}
          {data.status === "PARTIAL" && (
            <p>部分来源或匹配资料尚待补齐，已匹配的基金继续展示。</p>
          )}
          {pricesPending && (
            <p role="status">净值或技术位置资料待补齐，可稍后重新检查。</p>
          )}
          {isMatchingPending(data) && !query.isError && (
            <p role="status">
              {exhaustedLifecycle === lifecycle
                ? "自动检查已达上限，可手动重新检查主题归档或基金匹配。"
                : "主题归档或基金匹配正在完成，每3秒自动检查，最多10次；也可手动重新检查。"}
            </p>
          )}
          {[...data.items]
            .sort((a, b) => {
              const left = topicPriceOrder(a),
                right = topicPriceOrder(b);
              return left[0] - right[0] || right[1] - left[1];
            })
            .map((topic) =>
              presentation === "SUMMARY" ? (
                <TopicSummary key={topic.topicId} topic={topic} />
              ) : (
                <Topic key={topic.topicId} topic={topic} />
              ),
            )}
          {!data.items.length && confirmedZero && (
            <p>
              已归档的匹配结果为零；这仅表示该研究未匹配到基金，不代表市场没有机会。
            </p>
          )}
          {!data.items.length &&
            data.status !== "UNAVAILABLE" &&
            !isMatchingPending(data) &&
            !confirmedZero && (
              <p>基金匹配资料或归档状态尚待补齐，不能据此判断没有基金机会。</p>
            )}
        </>
      )}
      {terminal &&
        (query.isError ||
          (valid &&
            (pricesPending ||
              data.status === "UNAVAILABLE" ||
              (!data.items.length && !confirmedZero) ||
              isMatchingPending(data) ||
              data.missingEvidence?.includes("FUND_RECALL_FAILED")))) && (
          <button
            type="button"
            disabled={query.isFetching}
            onClick={() => {
              reads.current = 0;
              setExhaustedLifecycle(null);
              void query.refetch();
            }}
          >
            重新检查机会基金匹配
          </button>
        )}
    </>
  );
}

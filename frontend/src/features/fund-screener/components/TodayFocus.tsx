import type {
  ActionableZoneResult,
  OpportunityContext,
} from "../../../api/investment";
import { timeLabel } from "../macro-display";
import { marketJudgment } from "../today-focus-model";
import ObservationSection from "./ObservationSection";
import OpportunityFundReference from "./OpportunityFundReference";
import { quantitativePosition } from "../quantitative-position";
import "../today-focus.css";
import type { FundNavHistory } from "../../../api/funds";

export default function TodayFocus({
  data,
  onViewEvidence,
  frozenNavHistories,
}: {
  data: OpportunityContext;
  onViewEvidence: () => void;
  frozenNavHistories?: Record<string, FundNavHistory | null>;
}) {
  const daily = data.dailyResearch;
  const currentId = daily?.pipeline
    ? daily.pipeline.researchRunId
    : daily?.runId;
  const historical =
    daily?.state !== "READY" || daily?.reportRunId !== currentId;
  const sentences = marketJudgment(data);
  const directions = daily?.report?.valueDirections?.length
    ? daily.report.valueDirections
    : (daily?.economicResearch?.observations.valueDirections ?? []);
  const mentions =
    daily?.report?.mentionedFunds ??
    daily?.economicResearch?.observations.mentionedFunds ??
    [];
  const topics =
    daily?.report?.researchTopics ?? daily?.report?.topicCatalog ?? [];
  const directionPlans = directions.flatMap((direction) =>
    "plans" in direction && Array.isArray(direction.plans)
      ? (direction.plans as ActionableZoneResult[])
      : [],
  );
  const represented = new Set([
    ...directionPlans.map((plan) => plan.fundCode ?? plan.subjectId),
    ...mentions.map((fund) => fund.fundCode),
    ...topics.flatMap((topic) =>
      (topic.plans ?? topic.fundPlans ?? []).map(
        (plan) => plan.fundCode ?? plan.subjectId,
      ),
    ),
  ]);
  const aiOpportunityKeys = new Set([
    ...directions.map((direction, i) => direction.key ?? `direction-${i}`),
    ...topics.map((topic) => topic.key),
  ]);
  const scan = [...(daily?.report?.quantitativeScan?.items ?? [])]
    .filter((plan) => {
      const code = plan.fundCode ?? plan.subjectId;
      if (represented.has(code)) return false;
      const position = quantitativePosition(plan);
      const serverStatus = plan.pricePosition?.status;
      if (
        ["stop", "sell"].includes(position.tone) ||
        position.rank === 4 ||
        serverStatus === "DATA_PENDING" ||
        serverStatus === "OVERHEATED"
      )
        return false;
      const distances = (plan.signals ?? [])
        .map((signal) => signal.distanceToEntryPct)
        .filter(
          (distance) =>
            typeof distance === "number" &&
            Number.isFinite(distance) &&
            distance >= 0,
        );
      const inOrNearBuyZone = serverStatus
        ? serverStatus === "ENTRY_ZONE" || serverStatus === "NEAR_ENTRY_ZONE"
        : distances.length
          ? distances.some((distance) => distance <= 3)
          : position.buyDistance != null && position.buyDistance <= 3;
      const trend =
        plan.signals?.some(
          (signal) =>
            signal.track === "RIGHT_TREND" &&
            signal.triggerState === "CANDIDATE",
        ) ||
        plan.tracks.some(
          (track) =>
            track.track === "RIGHT_TREND" && track.state === "CANDIDATE",
        );
      if (!inOrNearBuyZone && !trend) return false;
      represented.add(code);
      return true;
    })
    .sort(
      (a, b) =>
        quantitativePosition(a).rank - quantitativePosition(b).rank ||
        Number(b.opportunityScore ?? 0) - Number(a.opportunityScore ?? 0),
    )
    .slice(0, Math.max(0, 15 - aiOpportunityKeys.size));
  return (
    <ObservationSection title="市场判断" className="op-today-judgment">
      {sentences.length ? (
        <>
          <small>
            {historical ? "历史研判" : "展示报告研判"} · 研究日期：
            {timeLabel(daily?.report?.asOf ?? daily?.reportCompletedAt)}
          </small>
          {sentences.map((sentence, index) => (
            <p key={index}>{sentence}</p>
          ))}
        </>
      ) : (
        <p>
          尚无有效报告研判，可查看已采集事实与独立事件；资料缺失不表示没有投资机会。
        </p>
      )}
      <button
        type="button"
        className="op-workspace-link"
        onClick={onViewEvidence}
      >
        查看完整研判与市场证据
      </button>
      {!!directions.length && (
        <section aria-label="AI价值方向">
          <h4>AI价值方向</h4>
          {directions.map((direction, index) => (
            <article
              className="op-insight-card"
              aria-label={direction.title}
              key={direction.key ?? index}
            >
              <strong>{direction.title}</strong>
              {"auditStatus" in direction &&
                direction.auditStatus === "UNAUDITED_SCOPE" && (
                  <small className="op-match-badge">基于已有资讯研判</small>
                )}
              <p>
                {direction.thesis ??
                  ("summary" in direction ? direction.summary : "")}
              </p>
              {"plans" in direction &&
                direction.plans.length > 0 &&
                direction.plans.map((plan) => (
                  <OpportunityFundReference
                    key={`${plan.subjectId}:${plan.selectedTrack ?? plan.track}`}
                    plan={plan}
                    frozenNavHistories={frozenNavHistories}
                  />
                ))}
            </article>
          ))}
        </section>
      )}
      {!!topics.length && (
        <section aria-label="全部AI研究主题">
          <h4>全部AI研究主题 · {topics.length}个</h4>
          {topics.map((topic, index) => (
            <article
              className="op-insight-card"
              key={topic.key ?? index}
              aria-label={topic.title}
            >
              <strong>{topic.title}</strong>
              <p>{topic.thesis ?? topic.summary}</p>
              {topic.auditStatus === "UNAUDITED_SCOPE" && (
                <small className="op-match-badge">基于已有资讯研判</small>
              )}
              {(topic.plans ?? topic.fundPlans ?? []).map((plan) => (
                <OpportunityFundReference
                  key={`${plan.subjectId}:${plan.selectedTrack ?? plan.track}`}
                  plan={plan}
                  frozenNavHistories={frozenNavHistories}
                />
              ))}
            </article>
          ))}
        </section>
      )}
      {!!mentions.length && (
        <section aria-label="本期提及基金">
          <h4>本期提及基金</h4>
          {mentions.map((fund) =>
            fund.actionableZones ? (
              <OpportunityFundReference
                key={fund.fundCode}
                plan={fund.actionableZones}
                name={fund.name ?? fund.label ?? "基金"}
                frozenNavHistories={frozenNavHistories}
              />
            ) : (
              <article className="op-insight-card" key={fund.fundCode}>
                <strong>
                  {fund.name ?? fund.label ?? "基金"} {fund.fundCode}
                </strong>
                <p>参考买卖点正在补齐净值资料。</p>
              </article>
            ),
          )}
        </section>
      )}
      {!!scan.length && (
        <section aria-label="量化模型精选机会">
          <h4>量化模型精选机会 · {scan.length}只</h4>
          <p>补充已入买区、距离买区不超过3%或右侧趋势已触发的真实扫描结果。</p>
          {scan.map((plan) => (
            <OpportunityFundReference
              key={plan.fundCode ?? plan.subjectId}
              plan={plan}
              frozenNavHistories={frozenNavHistories}
            />
          ))}
        </section>
      )}
    </ObservationSection>
  );
}

import type { OpportunityContext } from "../../../api/investment";
import { timeLabel } from "../macro-display";
import { marketJudgment } from "../today-focus-model";
import ObservationSection from "./ObservationSection";

export default function TodayFocus({
  data,
  onViewEvidence,
}: {
  data: OpportunityContext;
  onViewEvidence: () => void;
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
            <article key={direction.key ?? index}>
              <strong>{direction.title}</strong>
              <p>
                {direction.thesis ??
                  ("summary" in direction ? direction.summary : "")}
              </p>
              {"plans" in direction && direction.plans.length > 0 && (
                <p>
                  关联基金：
                  {direction.plans
                    .map(
                      (plan) =>
                        `${plan.label} ${plan.fundCode ?? plan.subjectId}`,
                    )
                    .join("、")}
                </p>
              )}
            </article>
          ))}
        </section>
      )}
      {!!mentions.length && (
        <section aria-label="本期提及基金">
          <h4>本期提及基金</h4>
          <p>
            {mentions
              .map(
                (fund) =>
                  `${fund.name ?? fund.label ?? "基金"} ${fund.fundCode}`,
              )
              .join(" · ")}
          </p>
        </section>
      )}
    </ObservationSection>
  );
}

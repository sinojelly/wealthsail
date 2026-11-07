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
    </ObservationSection>
  );
}

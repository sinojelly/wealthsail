import type { ActionableZoneResult } from "../../../api/investment";
import { timeLabel } from "../macro-display";
import { readingText } from "../research-reading";

const versionLabel = (version?: string | null) =>
  readingText(version?.replace(/^actionable-zones-/, "") ?? "版本待确认", 40);

export default function PlanPriceRefresh({
  plan,
}: {
  plan: ActionableZoneResult;
}) {
  const refresh = plan.priceRefresh;
  if (!refresh) return null;
  return (
    <div className="op-plan-time-basis">
      <p
        title={
          refresh.calculatedAt
            ? `重算时间：${timeLabel(refresh.calculatedAt)}`
            : undefined
        }
      >
        价位更新：净值日期{timeLabel(refresh.navAsOf)} ·{" "}
        {versionLabel(refresh.methodVersion)}
      </p>
      <p>
        研究日期：{timeLabel(plan.researchAsOf)}
        {plan.researchMethodVersion &&
          ` · ${versionLabel(plan.researchMethodVersion)}`}
      </p>
      {refresh.state === "PARTIAL" && (
        <p>价位依据不足：本地净值样本或覆盖尚未完整，当前区间仅供观察。</p>
      )}
      {refresh.state === "UNAVAILABLE" && (
        <p>价位暂不可用：本地有效净值不足，等待补齐后重算。</p>
      )}
    </div>
  );
}

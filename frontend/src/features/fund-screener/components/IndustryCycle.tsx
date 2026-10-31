import type { IndustryCycle } from "../../../api/investment";
import { readingText } from "../research-reading";

const stages = {
  EARNINGS_DELIVERY: { label: "🟢 业绩兑现期", tone: "earnings" },
  POLICY_CATALYST: { label: "🟡 政策/事件催化期", tone: "policy" },
  EARLY_COMMERCIALIZATION: {
    label: "🟠 商业化早期·去伪存真期",
    tone: "early",
  },
  UNKNOWN: { label: "产业阶段待核查", tone: "unknown" },
};

export function IndustryCycleBadge({
  cycle,
}: {
  cycle?: IndustryCycle | null;
}) {
  const stage = stages[cycle?.stage ?? "UNKNOWN"] ?? stages.UNKNOWN;
  return (
    <span
      aria-label="产业兑现阶段"
      className={`op-industry-cycle op-industry-cycle-${stage.tone}`}
      title={
        cycle?.evidenceSummary
          ? readingText(cycle.evidenceSummary, 300)
          : undefined
      }
    >
      {stage.label}
    </span>
  );
}

export function IndustryCycleWarning({
  cycle,
  warning,
}: {
  cycle?: IndustryCycle | null;
  warning?: string | null;
}) {
  const early = cycle?.stage === "EARLY_COMMERCIALIZATION";
  const message =
    warning?.trim() ||
    (early ? "⚠️ 行业去泡沫/等待订单期 · 谨防左侧接飞刀" : "");
  if (!message) return null;
  return (
    <div className="op-industry-risk" role="status" aria-label="产业周期风险">
      <strong>{readingText(message, 300)}</strong>
      {early && <p>仅适合极小仓位观察或等待右侧拐点。</p>}
    </div>
  );
}

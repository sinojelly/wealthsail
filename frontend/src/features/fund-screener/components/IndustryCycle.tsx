import type {
  DailyResearch,
  IndustryCycle,
  IndustryRiskCheck,
  IndustryRiskKind,
} from "../../../api/investment";
import { sourceHref } from "../macro-display";
import { readingText, sourceTitle } from "../research-reading";

const stages = {
  EARNINGS_DELIVERY: { label: "🟢 业绩兑现期", tone: "earnings" },
  POLICY_CATALYST: { label: "🟡 政策/事件催化期", tone: "policy" },
  EARLY_COMMERCIALIZATION: {
    label: "🟠 商业化早期·去伪存真期",
    tone: "early",
  },
  UNKNOWN: { label: "产业阶段待核查", tone: "unknown" },
};
const riskLevels = {
  NONE: { label: "行业风险：未触发", tone: "unknown" },
  UNPROVEN: { label: "行业风险：兑现待验证", tone: "policy" },
  SUSPECTED: { label: "行业风险：待核实", tone: "early" },
  CONFIRMED: { label: "行业风险：已确认", tone: "confirmed" },
};
const riskLabels: Record<IndustryRiskKind, string> = {
  FINANCING_COOLING: "融资降温",
  LAYOFFS_CLOSURES: "欠薪倒闭",
  ORDER_CUTS: "下游砍单",
  OVERCAPACITY_PRICE_WAR: "产能价格战",
  PRODUCTION_DELAY: "量产推迟",
};
const gapLabels: Record<string, string> = {
  CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING: "当期利润增长或真实回款证据不足",
  FORWARD_PLANS_ONLY: "仅见远期扩产或框架意向，尚无当期盈利兑现",
  NO_CURRENT_EARNINGS_PROOF: "当期盈利兑现缺少原文实证",
  PERSISTED_RISK_WATCH: "延续近期行业降温观察，等待盈利恢复证据",
};
type AuditSources = NonNullable<DailyResearch["report"]>["sources"];
function verificationLabel(check: IndustryRiskCheck) {
  if (check.verification === "SOURCE_CHECKED") return "已核对原文";
  if (check.verification === "UNAVAILABLE") return "原文暂不可用，待核验";
  return check.sourceIds.length ? "媒体线索 · 原文待核验" : "尚无可核验原文";
}

function IndustryAudit({
  cycle,
  sources,
}: {
  cycle: IndustryCycle;
  sources?: AuditSources;
}) {
  return (
    <details className="op-industry-audit-details">
      <summary>查看5项产业反方核查与证据明细</summary>
      {cycle.evidenceSummary && (
        <p>{readingText(cycle.evidenceSummary, 600)}</p>
      )}
      {typeof cycle.earningsVerified === "boolean" && (
        <p>
          当期利润与回款：
          {cycle.earningsVerified
            ? "原文证据已核实，适用范围以列示公司为准。"
            : "尚未同时核实，不能视为已兑现。"}
        </p>
      )}
      {(Object.entries(riskLabels) as Array<[IndustryRiskKind, string]>).map(
        ([kind, label]) => {
          const check = cycle.riskChecks?.find((row) => row.kind === kind);
          return (
            <section
              key={kind}
              aria-label={`${label}核查`}
              className="op-industry-audit-check"
            >
              <h5>{label}</h5>
              {check ? (
                <>
                  <p>
                    <strong>
                      {check.status === "FOUND"
                        ? "发现风险"
                        : check.status === "NOT_FOUND"
                          ? "本轮未检得显著负面"
                          : "待核验"}
                    </strong>{" "}
                    · {verificationLabel(check)}
                  </p>
                  {check.strength && (
                    <p>
                      证据强度：
                      {
                        {
                          CONFIRMED: "已确认",
                          SUSPECTED: "待核实",
                          UNPROVEN: "待验证",
                          NONE: "尚未确认",
                        }[check.strength]
                      }
                    </p>
                  )}
                  {typeof check.independentSourceCount === "number" &&
                    Number.isFinite(check.independentSourceCount) && (
                      <p>独立来源：{check.independentSourceCount}</p>
                    )}
                  <p>{readingText(check.summary, 400)}</p>
                  {check.status === "NOT_FOUND" && (
                    <p className="op-industry-audit-caveat">
                      本轮未检得不等于不存在风险，也不能证明检索已经充分。
                    </p>
                  )}
                  <p className="op-industry-audit-query">
                    检索词：{readingText(check.query, 350)}
                  </p>
                  {!!check.sourceIds.length && (
                    <p className="op-industry-audit-sources">
                      引用：
                      {[...new Set(check.sourceIds)].map((id, index) => {
                        const source = sources?.find((row) => row.id === id);
                        const href = source && sourceHref(source.url);
                        return href && source ? (
                          <a
                            key={id}
                            href={href}
                            target="_blank"
                            rel="noreferrer"
                          >
                            [{sourceTitle(source.title, 100)}]
                          </a>
                        ) : (
                          <span key={id}>
                            [来源{index + 1}：本页未提供原文链接]
                          </span>
                        );
                      })}
                    </p>
                  )}
                </>
              ) : (
                <p>未提供核查记录</p>
              )}
            </section>
          );
        },
      )}
      {!!cycle.evidenceGaps?.length && (
        <section aria-label="产业证据缺口" className="op-industry-audit-gaps">
          <h5>证据缺口</h5>
          <ul>
            {[...new Set(cycle.evidenceGaps)].map((gap) => (
              <li key={gap}>
                {riskLabels[gap as IndustryRiskKind]
                  ? `${riskLabels[gap as IndustryRiskKind]}证据仍待补齐`
                  : (gapLabels[gap] ?? readingText(gap, 300))}
              </li>
            ))}
          </ul>
        </section>
      )}
    </details>
  );
}

export function IndustryCycleBadge({
  cycle,
}: {
  cycle?: IndustryCycle | null;
}) {
  const stage = stages[cycle?.stage ?? "UNKNOWN"] ?? stages.UNKNOWN;
  const risk = cycle?.riskLevel ? riskLevels[cycle.riskLevel] : undefined;
  return (
    <>
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
      {risk && (
        <span
          aria-label="行业风险强度"
          className={`op-industry-cycle op-industry-cycle-${risk.tone}`}
        >
          {risk.label}
        </span>
      )}
    </>
  );
}

export function IndustryCycleWarning({
  cycle,
  warning,
  sources,
}: {
  cycle?: IndustryCycle | null;
  warning?: string | null;
  sources?: AuditSources;
}) {
  const early = cycle?.stage === "EARLY_COMMERCIALIZATION";
  const message =
    warning?.trim() ||
    cycle?.industryRiskWarning?.trim() ||
    (early ? "⚠️ 行业去泡沫/等待订单期 · 谨防左侧接飞刀" : "");
  const audit =
    !!cycle && (!!cycle.riskChecks?.length || !!cycle.evidenceGaps?.length);
  if (!message && !audit) return null;
  return (
    <>
      {message && (
        <div
          className="op-industry-risk"
          role="status"
          aria-label="产业周期风险"
        >
          <strong>{readingText(message, 300)}</strong>
          {early && <p>仅适合极小仓位观察或等待右侧拐点。</p>}
        </div>
      )}
      {audit && <IndustryAudit cycle={cycle!} sources={sources} />}
    </>
  );
}

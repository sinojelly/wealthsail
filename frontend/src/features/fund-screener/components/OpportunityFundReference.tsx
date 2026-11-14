import type { ActionableZoneResult } from "../../../api/investment";
import { fundDisplayName, planPrice, planUnit } from "../plan-display";
import { planNavSnapshot } from "../plan-nav-snapshot";
import { referenceTrack } from "../quantitative-position";
import { timeLabel } from "../macro-display";
import QuantitativePricePanel from "./QuantitativePricePanel";
import ViewportFundChart from "./ViewportFundChart";
import type { FundNavHistory } from "../../../api/funds";

/** Server-calculated reference levels remain readable for every research status. */
export default function OpportunityFundReference({
  plan,
  name = plan.label,
  frozenNavHistories,
  immediateChart = false,
}: {
  plan: ActionableZoneResult;
  name?: string;
  frozenNavHistories?: Record<string, FundNavHistory | null>;
  immediateChart?: boolean;
}) {
  const code = plan.fundCode ?? plan.subjectId;
  const historical = frozenNavHistories !== undefined;
  const snapshot = historical
    ? (frozenNavHistories[code] ?? null)
    : (planNavSnapshot(plan, name) ?? null);
  return (
    <section
      className="op-insight-fund"
      aria-label={`${name} ${code}参考买卖点`}
    >
      <header>
        <strong>
          {fundDisplayName(name, code)} {code}
        </strong>
      </header>
      <p>
        {historical ? "研究时确认净值" : "当前确认净值"}：
        <strong>{planPrice(plan.currentPrice, plan)}</strong>{" "}
        {planUnit(plan.unit)} · {timeLabel(plan.asOf)}
      </p>
      <div className="op-insight-fund-layout">
        <QuantitativePricePanel
          plan={plan}
          basis={historical ? "研究时" : "当前"}
        />
        {plan.priceBasis === "UNIT_NAV" && /^\d{6}$/.test(code) && (
          <ViewportFundChart
            fundCode={code}
            immediate={immediateChart}
            track={referenceTrack(plan)}
            confirmedNavAsOf={plan.asOf ?? undefined}
            {...(snapshot !== undefined
              ? {
                  snapshot,
                  snapshotMode: historical
                    ? ("HISTORICAL" as const)
                    : ("CURRENT_LOCAL" as const),
                }
              : {})}
          />
        )}
      </div>
    </section>
  );
}

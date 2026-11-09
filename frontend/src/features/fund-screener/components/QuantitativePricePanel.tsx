import type { ActionableZoneResult } from "../../../api/investment";
import { planPrice, planUnit } from "../plan-display";
import { timeLabel } from "../macro-display";
import { quantitativePosition } from "../quantitative-position";
import { ActionablePlanEvidence } from "./ActionablePlan";
import EstimatedProfitRange from "./EstimatedProfitRange";
import "../fund-price-tracks.css";

export default function QuantitativePricePanel({
  plan,
  basis = "当前",
}: {
  plan: ActionableZoneResult;
  basis?: string;
}) {
  const position = quantitativePosition(plan);
  const track = position.track;
  const price = (value: string | number | null | undefined) =>
    planPrice(value, plan);
  const percentile = plan.indicators?.navPercentile2y;
  const percent = (value: number | null) =>
    value == null ? "—" : `${value.toFixed(2)}%`;
  return (
    <section className="op-core-price-panel" aria-label="参考买卖点与操作状态">
      <header>
        <strong>参考买卖点与操作状态</strong>
        <span className="op-position-badge" data-tone={position.tone}>
          {position.label}
        </span>
      </header>
      <small>
        {basis}价格日期：{timeLabel(plan.asOf)} · {planUnit(plan.unit)}
      </small>
      <dl className="op-core-price-grid">
        <div>
          <dt>参考买入区间</dt>
          <dd>
            {price(track?.entryZoneMin)}–{price(track?.entryZoneMax)}
          </dd>
        </div>
        <div>
          <dt>参考卖出区间（止盈 T1 / T2）</dt>
          <dd>
            {price(track?.sellZoneMin ?? track?.takeProfitT1)}–
            {price(track?.sellZoneMax ?? track?.takeProfitT2)}
          </dd>
        </div>
        <div>
          <dt>风控止损参考</dt>
          <dd>{price(track?.stopLossPrice)}</dd>
        </div>
        <div>
          <dt>近2年所处分位</dt>
          <dd>
            {percentile != null && Number.isFinite(Number(percentile))
              ? `${Number(percentile).toFixed(1)}%`
              : "—"}
          </dd>
        </div>
        <div>
          <dt>距离买区 / 卖区</dt>
          <dd>
            {percent(position.buyDistance)} / {percent(position.sellDistance)}
          </dd>
        </div>
        {track && (
          <EstimatedProfitRange
            track={track}
            priceRefresh={plan.priceRefresh}
          />
        )}
      </dl>
      <details>
        <summary>价格依据与风险说明</summary>
        <ActionablePlanEvidence result={plan} />
      </details>
    </section>
  );
}

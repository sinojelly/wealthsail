import type { ActionableZoneResult, PlanTrack } from "../../../api/investment";
import { planPrice, planUnit } from "../plan-display";
import { timeLabel } from "../macro-display";
import "../fund-price-tracks.css";
import QuantitativePricePanel from "./QuantitativePricePanel";

/** Supplied prices only: selecting a track changes the chart, never permission. */
export default function FundPriceTracks({
  plan,
  basis,
  selected,
  onSelect,
}: {
  plan: ActionableZoneResult;
  basis: "当前" | "研究时" | "原冻结";
  selected?: PlanTrack;
  onSelect?: (track: PlanTrack) => void;
}) {
  if (basis === "当前" || plan.methodVersion.startsWith("actionable-zones-v6"))
    return <QuantitativePricePanel plan={plan} basis={basis} />;
  const price = (value: string | number | null | undefined) =>
    planPrice(value, plan);
  return (
    <div className="op-fund-price-summary">
      <small>
        {basis}价格日期：{timeLabel(plan.asOf)} · {planUnit(plan.unit)}
      </small>
      <div className="op-fund-price-tracks">
        {(
          [
            ["LEFT_VALUE", "左侧低吸"],
            ["RIGHT_TREND", "右侧趋势"],
          ] as const
        ).map(([key, label]) => {
          const track = plan.tracks.find((row) => row.track === key);
          return (
            <section
              key={key}
              className="op-fund-price-track"
              aria-label={`${basis}${label}价格区间`}
              data-selected={selected === key}
            >
              <header>
                {onSelect ? (
                  <button
                    type="button"
                    aria-pressed={selected === key}
                    onClick={() => onSelect(key)}
                  >
                    {label}
                  </button>
                ) : (
                  <strong>{label}</strong>
                )}
                <small>
                  {track
                    ? ({
                        CANDIDATE: "候选条件成立",
                        WATCH: "等待触发",
                        INSUFFICIENT: "证据不足",
                      }[track.state] ?? "待核验")
                    : "资料缺失"}
                </small>
              </header>
              {track ? (
                <>
                  <dl className="op-value-prices">
                    <div>
                      <dt>{basis}买入区间</dt>
                      <dd>
                        {price(track.entryZoneMin)}–{price(track.entryZoneMax)}
                      </dd>
                    </div>
                    <div>
                      <dt>{basis}卖出区间</dt>
                      <dd>
                        {price(track.sellZoneMin ?? track.takeProfitT1)}–
                        {price(track.sellZoneMax ?? track.takeProfitT2)}
                      </dd>
                    </div>
                    <div>
                      <dt>{basis}止损参考</dt>
                      <dd>{price(track.stopLossPrice)}</dd>
                    </div>
                    <div>
                      <dt>{basis}止盈 T1 / T2</dt>
                      <dd>
                        {price(track.takeProfitT1)} /{" "}
                        {price(track.takeProfitT2)}
                      </dd>
                    </div>
                  </dl>
                  {!!track.missingEvidence?.length && (
                    <p className="op-fund-price-gap">
                      缺少依据：{track.missingEvidence.join("；")}
                    </p>
                  )}
                </>
              ) : (
                <p className="op-fund-price-gap">
                  未保存{label}的完整买卖点，等待可核验价格资料。
                </p>
              )}
            </section>
          );
        })}
      </div>
      {onSelect && (
        <small>两条轨道的价格同时保留；点击轨道名称切换图中参考区间。</small>
      )}
    </div>
  );
}

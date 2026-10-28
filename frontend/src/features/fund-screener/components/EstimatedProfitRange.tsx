import type {
  ActionableZoneResult,
  ActionableZoneTrack,
  PlanPrice,
} from "../../../api/investment";

const percentage = (value: PlanPrice | undefined) => {
  if (
    value == null ||
    String(value).trim() === "" ||
    !Number.isFinite(Number(value))
  )
    return null;
  const amount = Number(value);
  return `${amount > 0 ? "+" : ""}${amount.toFixed(2)}%`;
};

/** Display server-computed bounds; old snapshots must not imply a zero return. */
export default function EstimatedProfitRange({
  track,
  priceRefresh,
}: {
  track: ActionableZoneTrack;
  priceRefresh?: ActionableZoneResult["priceRefresh"];
}) {
  if (priceRefresh && priceRefresh.state !== "READY") return null;
  const minimum = percentage(track.estimatedProfitMinPct);
  const maximum = percentage(track.estimatedProfitMaxPct);
  if (minimum === null || maximum === null) return null;
  const currentToMaximum = percentage(track.currentToSellMaxPct);
  return (
    <div className="op-value-profit">
      <dt>预估盈利区间</dt>
      <dd>
        {minimum} ~ {maximum}
      </dd>
      <small>按买区上沿→卖区下沿(保守) 至 买区下沿→卖区上沿(理想) 测算</small>
      {currentToMaximum !== null && (
        <small>当前至卖区上沿：{currentToMaximum}</small>
      )}
    </div>
  );
}

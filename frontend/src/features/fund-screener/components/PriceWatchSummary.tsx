import type { StudyPriceWatch } from "../../../api/study-api";
import { fundDisplayName, planPrice } from "../plan-display";
import { sourceHref, timeLabel } from "../macro-display";
import { readingText } from "../research-reading";

const labels = {
  ENTRY_ZONE: "🎯已入买入击球区",
  HOLDING: "📈持仓观察中",
  STOP_LOSS: "⚠️触及止损",
  TAKE_PROFIT: "🎉触及止盈",
  OBSERVING: "区间观察中",
  UNKNOWN: "行情待更新",
};
export default function PriceWatchSummary({
  watch,
}: {
  watch?: StudyPriceWatch | null;
}) {
  if (!watch) return null;
  const plan = watch.frozenPlan;
  const selected = plan?.selectedTrack ?? plan?.track;
  const track = plan?.tracks.find((row) => row.track === selected);
  const price = (value: number | string | null | undefined) =>
    planPrice(value, plan ?? { subjectKind: "nav", priceBasis: "UNIT_NAV" });
  const state =
    watch.state === "HOLDING" && watch.holding.state !== "CONFIRMED"
      ? "UNKNOWN"
      : watch.state;
  const observation = (
    row: StudyPriceWatch["confirmedNav"] | StudyPriceWatch["estimate1430"],
    label: string,
  ) => (
    <p>
      {label}：<strong>{price(row?.value)}</strong> · {timeLabel(row?.asOf)}
      {row && row.usable === false && (
        <small>仅保留历史记录，不用于当前触发。</small>
      )}
      {sourceHref(row?.sourceUri) && (
        <a href={sourceHref(row?.sourceUri)} target="_blank" rel="noreferrer">
          来源
        </a>
      )}
    </p>
  );
  return (
    <section className="op-price-watch" aria-label="冻结价位与当前行情">
      <strong className={`op-price-watch-state state-${state.toLowerCase()}`}>
        {labels[state]}
      </strong>
      {plan && (
        <p className="op-price-watch-name">
          {fundDisplayName(plan.label, plan.fundCode ?? plan.subjectId)}{" "}
          <span>{plan.fundCode ?? plan.subjectId}</span>
        </p>
      )}
      {track ? (
        <>
          <p>
            冻结买入区间：
            <strong>
              {price(track.entryZoneMin)}–{price(track.entryZoneMax)}
            </strong>
          </p>
          <p>
            止损 {price(track.stopLossPrice)} · T1 {price(track.takeProfitT1)} /
            T2 {price(track.takeProfitT2)}
          </p>
        </>
      ) : (
        <p>冻结价位尚无完整依据。</p>
      )}
      {observation(watch.confirmedNav, "最新确认净值")}
      {observation(watch.estimate1430, "14:30估算净值")}
      {watch.estimate1430?.checkedAt && (
        <small>估值核对时间：{timeLabel(watch.estimate1430.checkedAt)}</small>
      )}
      <small>
        当前判断：
        {watch.basis === "ESTIMATE_1430"
          ? "14:30估算净值（非最终净值）"
          : watch.basis === "CONFIRMED_NAV"
            ? "确认净值"
            : "行情待更新"}{" "}
        · {timeLabel(watch.asOf)}
      </small>
      {watch.reason && (
        <p className="op-price-watch-reason">
          {readingText(watch.reason, Infinity)}
        </p>
      )}
    </section>
  );
}

import { usePersonalFunds } from "../use-personal-funds";
import { fundDisplayName } from "../plan-display";
import { timeLabel } from "../macro-display";
import { watchStateLabels } from "../watch-display";
import "../my-funds.css";
import type { SellRiskAlert } from "../../../api/investment";
import {
  hasSameSellReminder,
  personalWatchState,
} from "../personal-fund-model";

export interface PersonalActionRemindersProps {
  actor: string;
  onViewFunds: () => void;
  sellRiskAlerts?: readonly SellRiskAlert[];
}
export default function PersonalActionReminders({
  actor,
  onViewFunds,
  sellRiskAlerts = [],
}: PersonalActionRemindersProps) {
  const { list, groups } = usePersonalFunds(actor);
  const observations = groups.flatMap((group) =>
    group.tracks.flatMap((track) => {
      const watch = track.priceWatch;
      const state = personalWatchState(watch);
      const sell =
        watch?.state === "STOP_LOSS" || watch?.state === "TAKE_PROFIT";
      const trigger = sell || watch?.state === "ENTRY_ZONE";
      const needsCheck =
        state === "UNKNOWN" ||
        track.status === "UNKNOWN" ||
        track.status === "MANUAL_REVIEW_REQUIRED" ||
        track.status === "INVALIDATED";
      const duplicate =
        sell &&
        !needsCheck &&
        hasSameSellReminder(group, track, sellRiskAlerts);
      return trigger || needsCheck
        ? [{ group, track, watch, needsCheck, sell, duplicate }]
        : [];
    }),
  );
  const hasGlobalDuplicates = observations.some((item) => item.duplicate);
  const entries = observations
    .filter((item) => !item.duplicate)
    .sort((a, b) => Number(b.sell) - Number(a.sell));
  const missing = groups.some((group) =>
    group.tracks.some(
      (track) =>
        !track.priceWatch || personalWatchState(track.priceWatch) === "UNKNOWN",
    ),
  );
  const unplannedHoldings = groups.filter(
    (group) =>
      group.holding &&
      !group.tracks.some((track) => track.priceWatch?.frozenPlan),
  );
  return (
    <section className="op-personal-reminders" aria-label="我的行动提醒">
      <header>
        <h3>我的行动提醒</h3>
        <button type="button" onClick={onViewFunds}>
          查看我的基金
        </button>
      </header>
      {list.isPending && <p role="status">正在读取个人行动提醒…</p>}
      {list.isError && (
        <p role="alert">
          行动提醒读取失败，无法确认当前触发。
          <button type="button" onClick={() => void list.refetch()}>
            重试行动提醒
          </button>
        </p>
      )}
      {!!entries.length && (
        <ul>
          {entries.map(({ group, track, watch, needsCheck, sell }) => (
            <li
              key={`${group.key}:${track.id}`}
              className={sell ? "op-reminder-risk" : ""}
            >
              <strong>
                {fundDisplayName(group.label, group.code)} ·{" "}
                {group.code ?? "代码待核对"}：
                {needsCheck
                  ? watch?.state === "HOLDING" &&
                    watch.holding?.state !== "CONFIRMED"
                    ? "持仓身份待核实"
                    : "待核实"
                  : watchStateLabels[watch!.state]}
              </strong>
              <span>
                {track.title} ·{" "}
                {group.market === "UNKNOWN" ? "市场待核实" : group.market} ·
                当前判断 {timeLabel(watch?.asOf)}
              </span>
              {watch?.reason && <p>{watch.reason}</p>}
            </li>
          ))}
        </ul>
      )}
      {list.isSuccess &&
        unplannedHoldings.map((group) => (
          <p role="status" key={group.key}>
            {fundDisplayName(group.label, group.code)}
            ：尚无可评估的冻结计划，无法确认持仓价格风险是否触发。
          </p>
        ))}
      {list.isSuccess && missing && (
        <p role="status">
          部分基金行情依据不足，暂无法确认是否触发；请在我的基金核对原计划。
        </p>
      )}
      {list.isSuccess && hasGlobalDuplicates && (
        <p>相同风险已在上方“持仓/跟踪卖出与风控提醒”展示。</p>
      )}
      {list.isSuccess &&
        !missing &&
        !unplannedHoldings.length &&
        !hasGlobalDuplicates &&
        !entries.length && <p>暂无新触发</p>}
    </section>
  );
}

import { usePersonalFunds } from "../use-personal-funds";
import { fundDisplayName } from "../plan-display";
import { timeLabel } from "../macro-display";
import { watchStateLabels } from "../watch-display";
import "../my-funds.css";

export interface PersonalActionRemindersProps {
  actor: string;
  onViewFunds: () => void;
  excludedSellFundCodes?: Iterable<string>;
}
export default function PersonalActionReminders({
  actor,
  onViewFunds,
  excludedSellFundCodes,
}: PersonalActionRemindersProps) {
  const { list, groups } = usePersonalFunds(actor);
  const excluded = new Set(excludedSellFundCodes ?? []);
  const entries = groups
    .flatMap((group) =>
      group.tracks.flatMap((track) => {
        const watch = track.priceWatch;
        const sell =
          watch?.state === "STOP_LOSS" || watch?.state === "TAKE_PROFIT";
        if (sell && group.code && excluded.has(group.code)) return [];
        const trigger = sell || watch?.state === "ENTRY_ZONE";
        const needsCheck =
          watch?.state === "UNKNOWN" ||
          track.status === "UNKNOWN" ||
          track.status === "MANUAL_REVIEW_REQUIRED" ||
          track.status === "INVALIDATED";
        return trigger || needsCheck
          ? [{ group, track, watch, needsCheck, sell }]
          : [];
      }),
    )
    .sort((a, b) => Number(b.sell) - Number(a.sell));
  const missing = groups.some((group) =>
    group.tracks.some((track) => !track.priceWatch),
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
                {needsCheck ? "待核实" : watchStateLabels[watch!.state]}
              </strong>
              <span>
                {track.title} · 当前判断 {timeLabel(watch?.asOf)}
              </span>
              {watch?.reason && <p>{watch.reason}</p>}
            </li>
          ))}
        </ul>
      )}
      {list.isSuccess && missing && (
        <p role="status">
          部分基金行情依据不足，暂无法确认是否触发；请在我的基金核对原计划。
        </p>
      )}
      {list.isSuccess && !missing && !entries.length && <p>暂无新触发</p>}
    </section>
  );
}

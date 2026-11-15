import { usePersonalFunds } from "../use-personal-funds";
import { fundDisplayName, planPrice } from "../plan-display";
import { timeLabel } from "../macro-display";
import { watchStateLabels } from "../watch-display";
import { referenceTrack } from "../quantitative-position";
import { fundOperationDistance } from "../fund-operation-distance";
import "../my-funds.css";
import type {
  SellRiskAlert,
  SellRiskAlerts,
  PersonalActionReminder,
} from "../../../api/investment";
import {
  hasSameSellReminder,
  personalWatchState,
} from "../personal-fund-model";

export interface PersonalActionRemindersProps {
  actor: string;
  onViewFunds: () => void;
  sellRiskAlerts?: readonly SellRiskAlert[];
  actionData?: SellRiskAlerts;
}
export default function PersonalActionReminders({
  actor,
  onViewFunds,
  sellRiskAlerts = [],
  actionData,
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
  const currentReminders = actionData?.actionReminders;
  const pendingHoldings = groups.filter(
    (group) =>
      group.holding &&
      !group.reference?.plan &&
      !group.tracks.some((track) => track.priceWatch?.frozenPlan),
  ).length;
  const normal = actionData?.normalHoldings ?? [];
  const currentPlans = new Map(
    groups
      .filter((group) => group.market === "FUND_CN" && group.reference?.plan)
      .map((group) => [group.code, group.reference!.plan]),
  );
  const price = (value: string | number | null | undefined) =>
    planPrice(value, { subjectKind: "nav", priceBasis: "UNIT_NAV" });
  const dynamicReference = (
    code: string | undefined,
    frozenAsOf: string | null | undefined,
    quoteAsOf: string | null | undefined,
  ) => {
    const plan = currentPlans.get(code);
    const track = plan && referenceTrack(plan);
    if (
      !plan?.asOf ||
      plan.priceBasis !== "UNIT_NAV" ||
      track?.priceState !== "READY" ||
      (plan.priceRefresh && plan.priceRefresh.state !== "READY") ||
      !frozenAsOf ||
      plan.asOf.slice(0, 10) < frozenAsOf.slice(0, 10) ||
      (quoteAsOf && plan.asOf.slice(0, 10) > quoteAsOf.slice(0, 10))
    )
      return null;
    return { plan, track };
  };
  const distance = (value: number | null) =>
    value != null && Number.isFinite(value) ? `${value.toFixed(2)}%` : "—";
  const reminderLabels = {
    BUY_ZONE: "🟢 低吸加仓参考",
    SELL_ZONE: "🟠 分批止盈参考",
    STOP_LOSS: "🔴 止损风控提醒",
    LARGE_DAILY_MOVE: "⚡ 当日大幅波动提醒",
    NORMAL: "区间中部观察",
  };
  const frozenFootnote = (
    day: string | null | undefined,
    low: PersonalActionReminder["entryZoneMin"] | undefined,
    high: PersonalActionReminder["entryZoneMax"] | undefined,
  ) =>
    day &&
    low != null &&
    high != null && (
      <small className="op-reminder-frozen-note">
        （原 {day.slice(5, 10)} 历史冻结买区：{price(low)}–{price(high)}）
      </small>
    );
  const references = (
    date: string | null | undefined,
    buyLow: PersonalActionReminder["entryZoneMin"] | undefined,
    buyHigh: PersonalActionReminder["entryZoneMax"] | undefined,
    sellLow: PersonalActionReminder["sellZoneMin"] | undefined,
    sellHigh: PersonalActionReminder["sellZoneMax"] | undefined,
    stop: PersonalActionReminder["stopLossPrice"] | undefined,
    frozen = false,
  ) => (
    <span className="op-reminder-dynamic-reference">
      {frozen ? "历史冻结参考" : "最新动态参考"}
      {date && `（${timeLabel(date)}）`} · 买入区间 {price(buyLow)}–
      {price(buyHigh)} · 卖出区间 {price(sellLow)}–{price(sellHigh)} · 止损{" "}
      {price(stop)}
    </span>
  );
  const legacyReference = (row: PersonalActionReminder) =>
    row.planOrigin === "FROZEN_STUDY"
      ? dynamicReference(row.fundCode, row.planAsOf, row.asOf)
      : null;
  const currentStatus = (
    row: PersonalActionReminder,
    dynamic: ReturnType<typeof legacyReference>,
  ): PersonalActionReminder["state"] => {
    if (!dynamic) return row.state;
    const position = fundOperationDistance(dynamic.plan, row.value);
    if (position.stopTriggered) return "STOP_LOSS";
    if (position.inSellZone) return "SELL_ZONE";
    if (
      position.inBuyZone &&
      !dynamic.plan.entryBlocked &&
      !dynamic.track.entryBlocked &&
      !dynamic.plan.subscriptionRestricted &&
      !dynamic.track.subscriptionRestricted
    )
      return "BUY_ZONE";
    return row.triggerStates.includes("LARGE_DAILY_MOVE")
      ? "LARGE_DAILY_MOVE"
      : "NORMAL";
  };
  const describe = (
    row: PersonalActionReminder,
    dynamic = legacyReference(row),
  ) => (
    <>
      {dynamic
        ? references(
            dynamic.plan.asOf,
            dynamic.track.entryZoneMin,
            dynamic.track.entryZoneMax,
            dynamic.track.sellZoneMin ?? dynamic.track.takeProfitT1,
            dynamic.track.sellZoneMax ?? dynamic.track.takeProfitT2,
            dynamic.track.stopLossPrice,
          )
        : row.planOrigin !== "NONE" &&
          references(
            row.planAsOf,
            row.entryZoneMin,
            row.entryZoneMax,
            row.sellZoneMin,
            row.sellZoneMax,
            row.stopLossPrice,
            row.planOrigin === "FROZEN_STUDY",
          )}
      {dynamic &&
        frozenFootnote(row.planAsOf, row.entryZoneMin, row.entryZoneMax)}
      {row.frozenReference &&
        frozenFootnote(
          row.frozenReference.asOf,
          row.frozenReference.entryZoneMin,
          row.frozenReference.entryZoneMax,
        )}
      <span>
        {row.basis === "CONFIRMED_NAV" ? "确认净值" : "估算净值"}{" "}
        {price(row.value)} · {timeLabel(row.asOf)}
      </span>
      {row.dailyChangePct != null &&
        Number.isFinite(Number(row.dailyChangePct)) && (
          <span>
            当日涨跌 {row.dailyChangePct > 0 ? "+" : ""}
            {Number(row.dailyChangePct).toFixed(2)}%
          </span>
        )}
    </>
  );
  return (
    <section className="op-personal-reminders" aria-label="我的行动提醒">
      <header>
        <h3>我的行动提醒</h3>
        <button type="button" onClick={onViewFunds}>
          查看我的基金
        </button>
      </header>
      {currentReminders === undefined && list.isPending && (
        <p role="status">正在读取个人行动提醒…</p>
      )}
      {currentReminders === undefined && list.isError && (
        <p role="alert">
          行动提醒读取失败，无法确认当前触发。
          <button type="button" onClick={() => void list.refetch()}>
            重试行动提醒
          </button>
        </p>
      )}
      {currentReminders !== undefined && (
        <>
          {!!currentReminders.length && (
            <ul>
              {currentReminders.map((row) => {
                const dynamic = legacyReference(row);
                const state = currentStatus(row, dynamic);
                return (
                  <li
                    key={`${row.fundCode}:${row.state}`}
                    className={state === "STOP_LOSS" ? "op-reminder-risk" : ""}
                  >
                    <strong>
                      {fundDisplayName(row.label, row.fundCode)} {row.fundCode}
                      ：{reminderLabels[state]}
                    </strong>
                    {describe(row, dynamic)}
                    {row.triggerStates
                      .filter(
                        (trigger) =>
                          trigger !== state &&
                          (!dynamic || trigger === "LARGE_DAILY_MOVE"),
                      )
                      .map((trigger) => (
                        <small key={trigger}>{reminderLabels[trigger]}</small>
                      ))}
                  </li>
                );
              })}
            </ul>
          )}
          {!!normal.length && (
            <details>
              <summary>
                其余 {normal.length}{" "}
                只持仓基金当前处于正常持有区间（点击展开查看全部持仓买卖点距离）
              </summary>
              <ul>
                {normal.map((row) => (
                  <li key={row.fundCode}>
                    <strong>
                      {row.label} {row.fundCode}
                    </strong>
                    {describe(row)}
                    <span>
                      距离买区 {distance(row.distanceToBuyPct)} · 距离卖区{" "}
                      {distance(row.distanceToSellPct)}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {!!actionData?.summary?.unavailableCount && (
            <p role="status">
              {actionData.summary.unavailableCount} 只持仓基金净值资料正在补齐。
            </p>
          )}
          {!currentReminders.length &&
            !normal.length &&
            !actionData?.summary?.unavailableCount && <p>暂无新触发</p>}
        </>
      )}
      {currentReminders === undefined && !!entries.length && (
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
                {watch?.frozenPlan?.asOf &&
                  ` · 原冻结计划 ${timeLabel(watch.frozenPlan.asOf)}`}
              </span>
              {watch?.reason && <p>{watch.reason}</p>}
            </li>
          ))}
        </ul>
      )}
      {currentReminders === undefined && list.isSuccess && missing && (
        <p role="status">
          部分基金行情依据不足，暂无法确认是否触发；请在我的基金核对原计划。
        </p>
      )}
      {currentReminders === undefined &&
        list.isSuccess &&
        pendingHoldings > 0 && (
          <p role="status">
            {pendingHoldings} 只持仓基金净值与参考买卖点正在补齐。
          </p>
        )}
      {currentReminders === undefined &&
        list.isSuccess &&
        hasGlobalDuplicates && (
          <p>相同风险已在上方“持仓/跟踪卖出与风控提醒”展示。</p>
        )}
      {list.isSuccess &&
        currentReminders === undefined &&
        !pendingHoldings &&
        !missing &&
        !hasGlobalDuplicates &&
        !entries.length && <p>暂无新触发</p>}
    </section>
  );
}

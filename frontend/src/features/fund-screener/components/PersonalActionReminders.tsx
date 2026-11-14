import { usePersonalFunds } from "../use-personal-funds";
import { fundDisplayName, planPrice } from "../plan-display";
import { timeLabel } from "../macro-display";
import { watchStateLabels } from "../watch-display";
import { referenceTrack } from "../quantitative-position";
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
  ) => {
    const plan = currentPlans.get(code);
    const track = plan && referenceTrack(plan);
    if (
      !plan?.asOf ||
      plan.priceBasis !== "UNIT_NAV" ||
      track?.priceState !== "READY" ||
      (plan.priceRefresh && plan.priceRefresh.state !== "READY") ||
      !frozenAsOf ||
      plan.asOf.slice(0, 10) <= frozenAsOf.slice(0, 10)
    )
      return null;
    return (
      <span className="op-reminder-dynamic-reference">
        最新动态参考（{timeLabel(plan.asOf)}） · 买入区间{" "}
        {price(track.entryZoneMin)}–{price(track.entryZoneMax)} · 卖出区间{" "}
        {price(track.sellZoneMin ?? track.takeProfitT1)}–
        {price(track.sellZoneMax ?? track.takeProfitT2)} · 止损{" "}
        {price(track.stopLossPrice)}
      </span>
    );
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
  const describe = (row: PersonalActionReminder) => (
    <>
      <span>
        {row.basis === "CONFIRMED_NAV" ? "确认净值" : "估算净值"}{" "}
        {price(row.value)} · {timeLabel(row.asOf)}
      </span>
      {row.planOrigin === "FROZEN_STUDY" &&
        dynamicReference(row.fundCode, row.planAsOf)}
      <span>
        {row.planOrigin === "FROZEN_STUDY" && (
          <>原冻结计划（{timeLabel(row.planAsOf)}，本提醒依据） · </>
        )}
        买入区间 {price(row.entryZoneMin)}–{price(row.entryZoneMax)} · 止盈 T1 /
        T2 {price(row.sellZoneMin)} / {price(row.sellZoneMax)} · 止损{" "}
        {price(row.stopLossPrice)}
      </span>
      {row.planOrigin !== "FROZEN_STUDY" && row.planAsOf && (
        <small>参考区间日期：{timeLabel(row.planAsOf)}</small>
      )}
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
              {currentReminders.map((row) => (
                <li
                  key={`${row.fundCode}:${row.state}`}
                  className={
                    row.state === "STOP_LOSS" ? "op-reminder-risk" : ""
                  }
                >
                  <strong>
                    {fundDisplayName(row.label, row.fundCode)} {row.fundCode}：
                    {reminderLabels[row.state]}
                  </strong>
                  {describe(row)}
                  {row.triggerStates
                    .filter((state) => state !== row.state)
                    .map((state) => (
                      <small key={state}>{reminderLabels[state]}</small>
                    ))}
                </li>
              ))}
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
              {group.market === "FUND_CN" &&
                dynamicReference(group.code, watch?.frozenPlan?.asOf)}
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

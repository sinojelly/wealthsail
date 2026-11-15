import { useEffect, useRef, useState, type ReactNode } from "react";
import Decimal from "decimal.js";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  getStudyDetail,
  setFundWatchStatus,
  setStudyStatus,
  type StudyPriceWatch,
  type StudyTrack,
} from "../../../api/study-api";
import {
  personalWatchState,
  personalFundPriority,
  sortPersonalFunds,
  relationshipLabels,
  type PersonalFundGroup,
} from "../personal-fund-model";
import { usePersonalFunds } from "../use-personal-funds";
import { fundDisplayName, planUnit } from "../plan-display";
import { finitePrice } from "../quantitative-position";
import { sourceHref, timeLabel } from "../macro-display";
import { currentFundEstimate, watchStateLabels } from "../watch-display";
import { studyConditionLabel, studyStates } from "./study-display";
import { FundWatchQuote } from "./PriceWatchSummary";
import ViewportFundChart from "./ViewportFundChart";
import FundReferenceDetails from "./FundReferenceDetails";
import FundPriceTracks from "./FundPriceTracks";
import OperationQuickTable, {
  type OperationQuickRow,
} from "./OperationQuickTable";
import "../my-funds.css";

export interface MyFundsWorkspaceProps {
  actor: string;
  renderStudyTracking?: () => ReactNode;
}

// The frozen-study price watcher accepts these equivalent yuan-per-unit labels.
const navUnits = new Set([
  "CNY",
  "元",
  "元/份",
  "CNY/unit",
  "CNY/份",
  "CNY/share",
]);

function newest<T extends { asOf: string | null; value: string | null }>(
  rows: Array<T | null | undefined>,
) {
  return rows.reduce<T | null>(
    (latest, row) =>
      row?.value != null && (!latest || (row.asOf ?? "") > (latest.asOf ?? ""))
        ? row
        : latest,
    null,
  );
}

function FundCardContent({
  group,
  actor,
  onReview,
  onCancel,
  pending,
}: {
  group: PersonalFundGroup;
  actor: string;
  onReview: (id: string) => void;
  onCancel: () => void;
  pending: boolean;
}) {
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const chosen =
    group.tracks.find((row) => row.id === chosenId) ?? group.tracks[0];
  const detail = useQuery({
    queryKey: ["study", actor, "detail", detailId, 0],
    queryFn: ({ signal }) => getStudyDetail(detailId!, signal),
    enabled: historyOpen && !!detailId,
  });
  const publicCode =
    group.market === "FUND_CN" && /^\d{6}$/.test(group.code ?? "")
      ? group.code
      : undefined;
  const watches = group.tracks.flatMap((row) =>
    row.priceWatch ? [row.priceWatch] : [],
  );
  const confirmed = newest<NonNullable<StudyPriceWatch["confirmedNav"]>>([
    group.reference?.confirmedNav,
    group.holding?.confirmedNav,
    ...watches.map((row) => row.confirmedNav),
  ]);
  const estimate = newest<NonNullable<StudyPriceWatch["currentEstimate"]>>([
    group.reference?.currentEstimate,
    group.holding?.currentEstimate,
    ...group.tracks.map((row) => row.currentEstimate),
    ...watches.map(currentFundEstimate),
  ]);
  const watch = chosen?.priceWatch;
  const plan = watch?.frozenPlan;
  const frozenTrack = plan?.tracks.find(
    (row) => row.track === (plan.selectedTrack ?? plan.track),
  );
  const overlay =
    plan?.priceBasis === "UNIT_NAV" && navUnits.has(plan.unit ?? "")
      ? frozenTrack
      : undefined;
  const state = personalWatchState(watch);
  const chosenDetail =
    detail.data?.track.id === chosen?.id ? detail.data : undefined;
  function select(track: StudyTrack) {
    setChosenId(track.id);
    setDetailId(track.id);
  }
  return (
    <div className="op-my-fund-body">
      <div className="op-my-fund-info">
        {!group.reference && (
          <>
            <FundWatchQuote row={confirmed} label="最新确认净值" />
            <FundWatchQuote
              row={estimate}
              label="可用估算净值（非最终净值）"
              estimate
            />
            {estimate?.checkedAt && (
              <small>估值核对时间：{timeLabel(estimate.checkedAt)}</small>
            )}
          </>
        )}
        {group.reference && <FundReferenceDetails fund={group.reference} />}
        {chosen && (
          <details
            className="op-my-fund-frozen-history op-print-disclosure"
            onToggle={(event) => setHistoryOpen(event.currentTarget.open)}
          >
            <summary>
              查看关联历史研究复盘（{chosen.createdAt.slice(0, 10)}）
            </summary>
            {historyOpen && (
              <>
                <div className="op-my-fund-plans" aria-label="独立冻结研究计划">
                  {group.tracks.map((track) => (
                    <button
                      type="button"
                      key={track.id}
                      aria-pressed={chosen?.id === track.id}
                      aria-label={`选择计划 ${track.title}`}
                      onClick={() => select(track)}
                    >
                      {track.title} · {timeLabel(track.createdAt)}
                    </button>
                  ))}
                </div>
                <section
                  aria-label="当前选择的冻结计划"
                  className="op-my-fund-plan"
                >
                  <strong>{chosen.title}</strong>
                  <small>
                    研究建立时间：{timeLabel(chosen.createdAt)} ·{" "}
                    {studyStates[chosen.status] ?? "状态待核对"}
                  </small>
                  {plan && (
                    <small>
                      计划行情基准：{timeLabel(plan.asOf)} · {plan.priceBasis} ·{" "}
                      {planUnit(plan.unit)} · {plan.methodVersion}
                      {sourceHref(plan.sourceUri) && (
                        <>
                          {" "}
                          ·{" "}
                          <a
                            href={sourceHref(plan.sourceUri)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            原计划来源
                          </a>
                        </>
                      )}
                    </small>
                  )}
                  {frozenTrack ? (
                    <>
                      <FundPriceTracks
                        plan={plan!}
                        basis="原冻结"
                        selected={frozenTrack.track}
                      />
                      <small>
                        冻结轨道：
                        {frozenTrack.track === "LEFT_VALUE"
                          ? "左侧低吸"
                          : "右侧趋势"}{" "}
                        · 研究时状态：
                        {frozenTrack.state === "CANDIDATE"
                          ? "候选"
                          : frozenTrack.state === "WATCH"
                            ? "观察"
                            : "资料待核对"}
                        。价位为原研究参考，未随最新行情改写。
                      </small>
                    </>
                  ) : (
                    <p>尚无完整冻结买卖点。</p>
                  )}
                  <p
                    className={`op-my-fund-state state-${state?.toLowerCase() ?? "unknown"}`}
                  >
                    <strong>
                      {state ? watchStateLabels[state] : "行情待更新"}
                    </strong>
                  </p>
                  <small>
                    当前判断依据：
                    {watch?.basis === "CONFIRMED_NAV"
                      ? "确认净值"
                      : watch?.basis === "ESTIMATE_1430"
                        ? "14:30估算净值"
                        : watch?.basis === "ESTIMATE_INTRADAY"
                          ? "盘中估算净值"
                          : "行情待更新"}{" "}
                    · {timeLabel(watch?.asOf)}
                  </small>
                  {watch?.reason && <p>{watch.reason}</p>}
                  {chosenDetail ? (
                    <p>
                      下一观察条件：
                      {chosenDetail.version.snapshot.watchFor[0] ??
                        (chosenDetail.version.snapshot.conditions[0]
                          ? studyConditionLabel(
                              chosenDetail.version.snapshot.conditions[0],
                            )
                          : "未声明，需人工复盘。")}{" "}
                      <small>
                        版本 {chosenDetail.version.version} ·{" "}
                        {timeLabel(chosenDetail.version.createdAt)}
                      </small>
                    </p>
                  ) : detailId === chosen.id && detail.isError ? (
                    <p role="alert">
                      该研究条件读取失败，未借用其他研究。
                      <button
                        type="button"
                        onClick={() => void detail.refetch()}
                      >
                        重试观察条件
                      </button>
                    </p>
                  ) : detailId === chosen.id && detail.isPending ? (
                    <p role="status">正在读取所选研究条件…</p>
                  ) : (
                    <p>
                      选择计划或
                      <button
                        type="button"
                        onClick={() => setDetailId(chosen.id)}
                      >
                        读取下一观察条件
                      </button>
                      后查看原研究条件。
                    </p>
                  )}
                  <button type="button" onClick={() => onReview(chosen.id)}>
                    研究与复盘：{chosen.title}
                  </button>
                </section>
              </>
            )}
          </details>
        )}
        {!chosen && (
          <p>
            {group.reference?.plan
              ? "当前程序参考价位独立展示；尚未保存个人冻结买卖计划。"
              : "持仓观察；尚未保存冻结买卖计划，未推算买卖条件。"}
          </p>
        )}
        {!group.trackingSuppressed &&
          (group.holding || group.tracks.length > 0) && (
            <button
              type="button"
              disabled={pending}
              onClick={onCancel}
              aria-label={`取消跟踪 ${group.code ?? group.label}`}
            >
              {pending ? "取消中…" : "取消跟踪"}
            </button>
          )}
      </div>
      {!group.reference && (
        <div className="op-my-fund-chart">
          {publicCode ? (
            <ViewportFundChart
              fundCode={publicCode}
              track={overlay}
              snapshot={null}
              snapshotMode="CURRENT_LOCAL"
              confirmedNavAsOf={confirmed?.asOf ?? undefined}
            />
          ) : (
            <p className="op-my-fund-chart-empty">
              净值图暂不可用：
              {group.code
                ? "当前市场尚未接入对应真实净值曲线。"
                : "基金代码尚未确认。"}
            </p>
          )}
          {!plan && publicCode && (
            <small>当前仅展示真实净值；尚无冻结买卖点。</small>
          )}
          {plan && !overlay && publicCode && (
            <small>原计划口径或单位未确认匹配，曲线不叠加该价位。</small>
          )}
        </div>
      )}
    </div>
  );
}

function personalBuyGuidance(
  group: PersonalFundGroup,
  priority: ReturnType<typeof personalFundPriority>,
) {
  const profit = group.reference?.holdingProfit;
  const raw = profit?.state === "AVAILABLE" ? profit.profitPct : null;
  if (
    raw == null ||
    raw.trim() === "" ||
    !priority.inBuyZone ||
    priority.stopTriggered ||
    priority.track?.priceState !== "READY" ||
    group.reference?.plan?.entryBlocked ||
    group.reference?.plan?.subscriptionRestricted ||
    priority.track?.entryBlocked ||
    priority.track?.subscriptionRestricted
  )
    return null;
  const change = Number(raw);
  if (change <= -20) {
    const location =
      priority.percentile == null
        ? "，近2年净值分位待补齐"
        : `，处于近2年 ${priority.percentile.toFixed(1)}% 分位`;
    return {
      tone: "deep",
      title: "🟡 深套控仓提醒",
      detail: `当前浮亏 ${change.toFixed(2)}%${location}。若该赛道仓位已重，先观察净值站上20日均线并企稳，再评估分批摊薄。`,
      advice: "赛道仓位较重时，等待净值站上20日均线后复核。",
    };
  }
  if (!Number.isFinite(change) || change <= -15) return null;
  const lowPercentile =
    priority.percentile != null && priority.percentile <= 35;
  return {
    tone: "healthy",
    title: "🟢 优选低吸加仓",
    detail: `底仓成本相对健康 · ${lowPercentile ? "处于低分位买区" : "已进入参考买区"}。仓位允许时可分批评估，留意止损参考。`,
    advice: "底仓成本相对健康，仓位允许时可分批评估。",
  };
}

function FundCard({
  requestedOpen,
  ...props
}: Parameters<typeof FundCardContent>[0] & { requestedOpen?: number }) {
  const { group } = props;
  const priority = personalFundPriority(group);
  const [choice, setChoice] = useState<{ open: boolean; request?: number }>();
  const open =
    choice && choice.request === requestedOpen
      ? choice.open
      : requestedOpen !== undefined || priority.defaultExpanded;
  const card = useRef<HTMLElement>(null);
  useEffect(() => {
    if (requestedOpen === undefined) return;
    const element = card.current;
    element?.querySelector("button")?.focus({ preventScroll: true });
    element?.scrollIntoView?.({
      block: "start",
      behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
  }, [requestedOpen]);
  const profit = group.reference?.holdingProfit;
  const guidance = personalBuyGuidance(group, priority);
  const signed = (value: string) =>
    `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(2)}`;
  const price = (value: number | string | null | undefined) =>
    value == null || !Number.isFinite(Number(value))
      ? "—"
      : Number(value).toFixed(4);
  return (
    <article
      className="op-my-fund-card"
      ref={card}
      aria-label={group.label}
      data-expanded={open}
    >
      <button
        type="button"
        className="op-my-fund-toggle"
        aria-label={`${open ? "收起" : "展开"} ${group.label}详情与走势`}
        aria-expanded={open}
        onClick={() => setChoice({ open: !open, request: requestedOpen })}
      >
        <span className="op-my-fund-heading-line">
          <strong>{fundDisplayName(group.label, group.code)}</strong>
          <span className="op-my-fund-status">{priority.label}</span>
        </span>
        <span className="op-my-fund-badges">
          {group.holding && <span>已持仓</span>}
          {!!group.tracks.length && (
            <span>
              {group.holding ? "同时跟踪" : "仅跟踪"} · {group.tracks.length}
              份独立研究
            </span>
          )}
          {group.trackingSuppressed && <span>已取消跟踪</span>}
          {group.reference?.relationships
            .filter((key) => key === "WATCHLIST")
            .map((key) => (
              <span key={key}>{relationshipLabels[key]}</span>
            ))}
          <small>
            {group.code ?? "代码待核对"} ·{" "}
            {group.market === "UNKNOWN" ? "市场待核对" : group.market}
          </small>
        </span>
        <span className="op-my-fund-summary-prices">
          当前净值 {price(priority.current)} · 参考买区 {price(priority.low)}–
          {price(priority.high)} · 参考卖区 {price(priority.sellLow)}–
          {price(priority.sellHigh)}
        </span>
        <span className="op-my-fund-toggle-label">
          {open ? "▾ 收起详情与走势" : "▸ 展开详情与走势"}
        </span>
      </button>
      {group.holding && (
        <div
          className={`op-my-fund-profit ${profit?.profitPct != null && Number(profit.profitPct) > 0 ? "positive" : profit?.profitPct != null && Number(profit.profitPct) < 0 ? "negative" : ""}`}
        >
          <strong>
            持仓参考盈亏：
            {profit?.state === "AVAILABLE" && profit.profitPct != null ? (
              <>
                {signed(profit.profitPct)}%
                {profit.profitAmount != null &&
                  `（¥${signed(profit.profitAmount)}）`}
              </>
            ) : (
              "暂不可计算"
            )}
          </strong>
          <small>
            {profit?.unitCost ? (
              <>持仓成本 {price(profit.unitCost)} 元/份 · </>
            ) : (
              "成本资料待补齐 · "
            )}
            {profit?.latestNav ? (
              <>
                最新{profit.priceBasis === "ESTIMATED_NAV" ? "估值" : "净值"}{" "}
                {price(profit.latestNav)}（{timeLabel(profit.priceAsOf)}）
              </>
            ) : (
              "暂无对应价格"
            )}
          </small>
          {profit?.priceBasis === "ESTIMATED_NAV" && (
            <small>基于估算净值，仅供参考。</small>
          )}
          {guidance && (
            <p className="op-my-fund-guidance" data-tone={guidance.tone}>
              <b>{guidance.title}</b>：{guidance.detail}
            </p>
          )}
        </div>
      )}
      {open && <FundCardContent {...props} />}
    </article>
  );
}

const currency = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  currencyDisplay: "narrowSymbol",
});
const signedCurrency = new Intl.NumberFormat("zh-CN", {
  style: "currency",
  currency: "CNY",
  currencyDisplay: "narrowSymbol",
  signDisplay: "exceptZero",
});
const signedPercent = new Intl.NumberFormat("zh-CN", {
  style: "percent",
  signDisplay: "exceptZero",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const positiveMoney = (value?: string | null) =>
  value && finitePrice(value) != null ? new Decimal(value) : null;

function HoldingOverview({ groups }: { groups: PersonalFundGroup[] }) {
  const holdings = groups.filter((group) => group.holding);
  let totalCost = new Decimal(0);
  let marketValue = new Decimal(0);
  let comparedCost = new Decimal(0);
  let comparedValue = new Decimal(0);
  let costCount = 0,
    pricedCount = 0,
    comparableCount = 0,
    estimatedCount = 0;
  let buy = 0,
    sell = 0;
  for (const group of holdings) {
    const profit = group.reference?.holdingProfit;
    const cost = positiveMoney(profit?.totalCost);
    const shares = positiveMoney(profit?.shares);
    const nav = positiveMoney(profit?.latestNav);
    const value = shares && nav ? shares.times(nav) : null;
    if (cost) {
      totalCost = totalCost.plus(cost);
      costCount += 1;
    }
    if (value) {
      marketValue = marketValue.plus(value);
      pricedCount += 1;
      if (profit?.priceBasis === "ESTIMATED_NAV") estimatedCount += 1;
      if (cost) {
        comparedCost = comparedCost.plus(cost);
        comparedValue = comparedValue.plus(value);
        comparableCount += 1;
      }
    }
    if (group.reference?.plan) {
      const position = personalFundPriority({ ...group, tracks: [] });
      if (!position.stopTriggered && position.inBuyZone) buy += 1;
      if (position.nearSell) sell += 1;
    }
  }
  const profit = comparedValue.minus(comparedCost);
  const complete = comparableCount === holdings.length;
  const money = (value: Decimal, count: number) =>
    count || !holdings.length ? currency.format(value.toNumber()) : "—";
  return (
    <section
      className="op-holding-overview"
      aria-labelledby="my-holding-overview"
    >
      <h4 id="my-holding-overview">我的持仓总览</h4>
      <dl>
        <div>
          <dt>持仓基金</dt>
          <dd>{holdings.length} 只</dd>
        </div>
        <div>
          <dt>总持仓成本</dt>
          <dd>{money(totalCost, costCount)}</dd>
        </div>
        <div>
          <dt>参考总市值</dt>
          <dd>{money(marketValue, pricedCount)}</dd>
        </div>
        <div>
          <dt>参考总盈亏</dt>
          <dd>
            {comparableCount
              ? `${signedCurrency.format(profit.toNumber())}（${signedPercent.format(profit.div(comparedCost).toNumber())}）`
              : "—"}
          </dd>
        </div>
        <div>
          <dt>今日已入买区</dt>
          <dd>{buy} 只</dd>
        </div>
        <div>
          <dt>接近或已入卖区</dt>
          <dd>{sell} 只</dd>
        </div>
      </dl>
      {(!complete || estimatedCount > 0) && (
        <small>
          {!complete &&
            `成本覆盖 ${costCount}/${holdings.length} 只，市值覆盖 ${pricedCount}/${holdings.length} 只，盈亏按两项均可得的 ${comparableCount} 只计算。`}
          {estimatedCount > 0 && `其中 ${estimatedCount} 只使用估算净值。`}
        </small>
      )}
    </section>
  );
}

function quickOperationRow(group: PersonalFundGroup): OperationQuickRow | null {
  const plan = group.reference?.plan;
  if (!plan) return null;
  const position = personalFundPriority(group);
  if (!position.track || position.track.priceState !== "READY") return null;
  const guidance = personalBuyGuidance(group, position);
  const entryBlocked =
    plan.entryBlocked ||
    plan.subscriptionRestricted ||
    position.track.entryBlocked ||
    position.track.subscriptionRestricted;
  const action = position.stopTriggered
    ? { tone: "stop", advice: "暂停补仓，复核止损条件。" }
    : position.inBuyZone
      ? {
          tone: "buy",
          advice: entryBlocked
            ? "先核对申购限制和入场条件。"
            : (guidance?.advice ?? "按仓位与止损参考分批评估。"),
        }
      : position.nearSell
        ? { tone: "sell", advice: "复核持仓止盈计划与卖区价位。" }
        : position.nearBuy
          ? { tone: "near", advice: "观察最新净值是否落入参考买区。" }
          : null;
  if (!action) return null;
  return {
    id: group.key,
    name: fundDisplayName(group.label, group.code),
    code: group.code ?? "",
    plan,
    currentNav: position.current,
    status:
      position.inBuyZone || position.stopTriggered
        ? position.label
        : position.inSellZone
          ? "已入卖区"
          : position.nearSell
            ? `接近卖区（差 ${position.sellDistance!.toFixed(2)}%）`
            : position.label,
    ...action,
    holdingReturnPct:
      group.reference?.holdingProfit?.state === "AVAILABLE"
        ? group.reference.holdingProfit.profitPct
        : null,
  };
}

export default function MyFundsWorkspace(props: MyFundsWorkspaceProps) {
  return <Workspace key={props.actor} {...props} />;
}

function Workspace({ actor, renderStudyTracking }: MyFundsWorkspaceProps) {
  const { list, reference, groups, refresh } = usePersonalFunds(actor);
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [openRequest, setOpenRequest] = useState<{
    id: string;
    request: number;
  }>();
  const term = search.trim().toLocaleLowerCase();
  const matches = (row: PersonalFundGroup) =>
    !term ||
    `${row.label} ${row.code ?? ""} ${row.holding?.instrumentId ?? ""}`
      .toLocaleLowerCase()
      .includes(term);
  const filtered = sortPersonalFunds(groups).filter(
    (row) =>
      (filter !== "held" || row.holding) &&
      (filter !== "tracked" ||
        (!row.holding &&
          (row.tracks.length ||
            row.reference?.relationships.includes("TRACKED")))) &&
      (filter !== "watchlist" ||
        row.reference?.relationships.includes("WATCHLIST")) &&
      matches(row),
  );
  const history = (list.data?.items ?? []).filter(
    (track) =>
      !term ||
      `${track.title} ${track.scope.fundCode ?? ""} ${track.scope.instrumentId ?? ""}`
        .toLocaleLowerCase()
        .includes(term),
  );
  const quickRows = sortPersonalFunds(groups).flatMap((group) => {
    const row = quickOperationRow(group);
    return row ? [row] : [];
  });
  function openFromTable(id: string) {
    setFilter("all");
    setSearch("");
    setOpenRequest((previous) => ({
      id,
      request: (previous?.request ?? 0) + 1,
    }));
  }
  function review(id: string) {
    setReviewOpen(true);
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("studyTrack", id);
      next.set("researchTab", "tracking");
      next.set("researchOpen", "1");
      next.delete("researchData");
      return next;
    });
  }
  async function cancel(group: PersonalFundGroup) {
    if (pending) return;
    setPending(group.key);
    setError("");
    setNotice("");
    try {
      const knownMarket =
        group.market !== "UNKNOWN" &&
        /^[A-Z][A-Z0-9_]{0,31}$/.test(group.market);
      if (knownMarket && /^\d{6}$/.test(group.code ?? ""))
        await setFundWatchStatus({
          fundCode: group.code,
          fundMarket: group.market,
          enabled: false,
        });
      else if (group.tracks.length)
        for (const track of group.tracks)
          await setStudyStatus(track.id, {
            expectedGeneration: track.generation,
            enabled: false,
          });
      else if (knownMarket && group.holding?.instrumentId)
        await setFundWatchStatus({
          instrumentId: group.holding.instrumentId,
          fundMarket: group.market,
          enabled: false,
        });
      else
        throw new Error(
          "基金市场或标的身份尚未确认，无法取消跟踪；持仓记录保留。",
        );
      setNotice(
        group.holding
          ? "已取消跟踪，持仓记录保留。"
          : "已取消跟踪，历史研究记录保留。",
      );
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "取消跟踪失败，请重试。",
      );
    } finally {
      // A preceding study may already be disabled when a later request fails.
      // Refresh its generation without replacing the original mutation error.
      await client
        .invalidateQueries({ queryKey: ["study", actor] })
        .catch(() => undefined);
      await reference.refetch().catch(() => undefined);
      setPending(null);
    }
  }
  const showReview = reviewOpen || !!params.get("studyTrack");
  return (
    <section className="op-my-funds" aria-label="我的基金">
      <header className="op-my-funds-heading">
        <div>
          <h3>我的基金</h3>
          <p>
            只展示持仓、自选关注与活动跟踪。按操作距离排序，次要项可展开查看完整详情与走势。
          </p>
        </div>
        <button
          type="button"
          disabled={refreshing}
          onClick={async () => {
            setRefreshing(true);
            setError("");
            try {
              await refresh();
            } catch (failure) {
              setError(
                failure instanceof Error
                  ? failure.message
                  : "刷新未完成，保留本地已有记录。",
              );
            } finally {
              setRefreshing(false);
            }
          }}
        >
          {refreshing ? "正在刷新我的基金…" : "刷新我的基金"}
        </button>
      </header>
      {(list.isSuccess || reference.isSuccess) && (
        <>
          <HoldingOverview groups={groups} />
          <OperationQuickTable rows={quickRows} onSelect={openFromTable} />
        </>
      )}
      <div className="op-my-funds-toolbar">
        <nav aria-label="我的基金筛选">
          {[
            ["all", "全部我的基金"],
            ["held", "已持仓"],
            ["tracked", "仅跟踪"],
            ["watchlist", "自选关注"],
            ["history", "历史复盘"],
          ].map(([key, label]) => (
            <button
              type="button"
              key={key}
              aria-pressed={filter === key}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
        </nav>
        <input
          type="search"
          aria-label="搜索我的基金"
          placeholder="搜索基金名称或代码"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {list.isPending && <p role="status">正在读取我的基金…</p>}
      {list.isError && (
        <p role="alert">
          我的基金读取失败；已有记录可能尚未更新。
          <button type="button" onClick={() => void list.refetch()}>
            重试我的基金
          </button>
        </p>
      )}
      {reference.isError && (
        <p role="status">自选基金参考价位暂未补齐，已有持仓和跟踪仍可查看。</p>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {filter === "history" ? (
        <section aria-label="全部研究与历史复盘">
          <p>包含活动、已结束、停用及非基金研究；原版本和复盘继续保留。</p>
          <ul className="op-my-funds-history">
            {history.map((track) => (
              <li key={track.id}>
                <button type="button" onClick={() => review(track.id)}>
                  {track.title} · {studyStates[track.status] ?? "状态待核对"}
                  {!track.enabled ? " · 已停用" : ""} ·{" "}
                  {timeLabel(track.createdAt)}
                </button>
              </li>
            ))}
          </ul>
          {list.isSuccess && !history.length && <p>暂无匹配的历史研究。</p>}
        </section>
      ) : (
        <>
          {filtered.map((group) => (
            <FundCard
              key={group.key}
              group={group}
              actor={actor}
              onReview={review}
              onCancel={() => void cancel(group)}
              pending={pending !== null}
              requestedOpen={
                openRequest?.id === group.key ? openRequest.request : undefined
              }
            />
          ))}
          {list.isSuccess && !reference.isPending && !filtered.length && (
            <p>
              {groups.length
                ? "没有匹配的基金，请调整筛选或搜索。"
                : "暂无持仓、活动跟踪或自选基金；历史研究仍可查看。"}
            </p>
          )}
        </>
      )}
      {renderStudyTracking && (
        <section className="op-my-funds-review" aria-label="研究与复盘">
          <button
            type="button"
            aria-expanded={showReview}
            onClick={() => {
              if (showReview) {
                setReviewOpen(false);
                setParams((previous) => {
                  const next = new URLSearchParams(previous);
                  next.delete("studyTrack");
                  return next;
                });
              } else setReviewOpen(true);
            }}
          >
            {showReview ? "收起研究与复盘" : "打开研究与复盘 / 新建研究"}
          </button>
          {showReview && renderStudyTracking()}
        </section>
      )}
    </section>
  );
}

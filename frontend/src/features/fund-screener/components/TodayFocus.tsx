import { useMemo, useState } from "react";
import type { FundNavHistory } from "../../../api/funds";
import type {
  ActionableZoneResult,
  OpportunityContext,
} from "../../../api/investment";
import { buildTodayBoard, type TodayFund } from "../today-action-board";
import { fundDisplayName, planPrice } from "../plan-display";
import { finitePrice, referenceTrack } from "../quantitative-position";
import { sourceHref } from "../macro-display";
import { todayFundReading } from "../today-fund-reading";
import ObservationSection from "./ObservationSection";
import OpportunityFundReference from "./OpportunityFundReference";
import QuantitativePricePanel from "./QuantitativePricePanel";
import MacroCycleOverview from "./MacroCycleOverview";
import OperationQuickTable from "./OperationQuickTable";
import "../today-focus.css";

function fundLink(key: string) {
  return `today-fund-${encodeURIComponent(key)}`;
}

function FundLink({
  fund,
  reveal,
}: {
  fund: TodayFund;
  reveal: (key: string) => void;
}) {
  const id = fundLink(fund.key);
  return (
    <a href={`#${encodeURIComponent(id)}`} onClick={() => reveal(fund.key)}>
      {fundDisplayName(fund.name, fund.code)} {fund.code}
    </a>
  );
}

function trendChange(value: unknown, factor: number, unit: string) {
  if (value == null || value === "" || !Number.isFinite(Number(value)))
    return "待补齐";
  const change = Number(value) * factor;
  return `${change > 0 ? "+" : ""}${change.toFixed(2)}${unit}`;
}

function RightTrendEvidence({ plan }: { plan: ActionableZoneResult }) {
  const indicators = plan.indicators ?? {};
  const ma20 = finitePrice(indicators.ma20);
  const ma60 = finitePrice(indicators.ma60);
  const rsi = indicators.rsi14 == null ? null : Number(indicators.rsi14);
  const excess =
    plan.relativeStrength?.excessReturn20d ?? indicators.excessReturn20d;
  return (
    <span className="op-today-fund-evidence">
      <b>右侧趋势观察</b>
      <span>
        {ma20 != null && ma60 != null
          ? `MA20 ${planPrice(ma20, plan)} ${ma20 > ma60 ? ">" : ma20 < ma60 ? "<" : "="} MA60 ${planPrice(ma60, plan)}`
          : "MA20 / MA60 待补齐"}
      </span>
      <span>
        RSI14 {rsi != null && Number.isFinite(rsi) ? rsi.toFixed(1) : "待补齐"}
      </span>
      <span>近20日 {trendChange(indicators.momentum20, 100, "%")}</span>
      <span>
        较{plan.relativeStrength?.benchmark ?? "沪深300"}{" "}
        {trendChange(excess, 1, " 个百分点")}
      </span>
    </span>
  );
}

function FundRow({
  fund,
  expanded,
  onExpand,
  frozenNavHistories,
}: {
  fund: TodayFund;
  expanded: boolean;
  onExpand: (value: boolean) => void;
  frozenNavHistories?: Record<string, FundNavHistory | null>;
}) {
  const plan = fund.plans[0];
  const track = referenceTrack(plan);
  const rationale = todayFundReading(fund);
  const backtest = track?.buyZoneBacktest;
  const sources =
    fund.priority.tone === "sell" || fund.priority.tone === "stop"
      ? []
      : (plan.macroOpportunity?.sourceUris?.map(sourceHref).filter(Boolean) ??
        []);
  return (
    <details
      className="op-today-fund"
      id={fundLink(fund.key)}
      data-testid="today-fund-row"
      data-fund-code={fund.code}
      open={expanded}
      onToggle={(e) => {
        if (e.currentTarget.open !== expanded) onExpand(e.currentTarget.open);
      }}
    >
      <summary
        onClick={(e) => {
          e.preventDefault();
          onExpand(!expanded);
        }}
      >
        <span className="op-position-badge" data-tone={fund.priority.tone}>
          {fund.priority.label}
        </span>
        <strong className="op-today-fund-name">
          {fundDisplayName(fund.name, fund.code)} {fund.code}
        </strong>
        <span className="op-today-summary-prices">
          净值 {planPrice(plan.currentPrice, plan)} · 买区{" "}
          {planPrice(track?.entryZoneMin, plan)}–
          {planPrice(track?.entryZoneMax, plan)}
        </span>
        {fund.priority.tone === "trend" && <RightTrendEvidence plan={plan} />}
      </summary>
      {expanded && (
        <div className="op-today-fund-body">
          <section className="op-today-reading" aria-label="投研结论与操作理由">
            <h4>💡 投研结论与操作理由</h4>
            <dl>
              <div>
                <dt>价格与赔率</dt>
                <dd>{rationale.price}</dd>
              </div>
              <div>
                <dt>基本面与催化</dt>
                <dd>
                  {rationale.industry}
                  {sources.map((source, index) => (
                    <a
                      key={source}
                      href={source}
                      target="_blank"
                      rel="noreferrer"
                    >
                      依据 {index + 1}
                    </a>
                  ))}
                </dd>
              </div>
              <div>
                <dt>操作与风控</dt>
                <dd>{rationale.advice}</dd>
              </div>
            </dl>
          </section>
          {backtest && (
            <p className="op-today-backtest">
              📊 近两年买区回测 · 按当前区间回看触及 {backtest.touchCount} 次，
              {backtest.winRatePct != null &&
              backtest.averageMaxReboundPct != null ? (
                <>
                  其中 {backtest.evaluatedCount} 次有完整后续{" "}
                  {backtest.forwardObservations} 个净值观测， 反弹超过{" "}
                  {backtest.winThresholdPct}% 的比例{" "}
                  {backtest.winRatePct.toFixed(1)}%， 平均最大反弹{" "}
                  {trendChange(backtest.averageMaxReboundPct, 1, "%")}。
                </>
              ) : (
                `完整后续 ${backtest.forwardObservations} 个净值观测样本不足，暂未计算反弹比例。`
              )}
            </p>
          )}
          <OpportunityFundReference
            plan={plan}
            name={fund.name}
            frozenNavHistories={frozenNavHistories}
            displayStatus={fund.priority}
            immediateChart
          />
          {fund.plans.length > 1 && (
            <details className="op-today-alternatives">
              <summary>其它研究价位方案 · {fund.plans.length - 1} 项</summary>
              {fund.plans.slice(1).map((p, i) => (
                <QuantitativePricePanel key={i} plan={p} />
              ))}
            </details>
          )}
        </div>
      )}
    </details>
  );
}

export default function TodayFocus({
  data,
  onViewEvidence,
  frozenNavHistories,
  watchedCodes,
}: {
  data: OpportunityContext;
  onViewEvidence: () => void;
  frozenNavHistories?: Record<string, FundNavHistory | null>;
  watchedCodes?: readonly string[];
}) {
  const board = useMemo(
    () => buildTodayBoard(data, watchedCodes),
    [data, watchedCodes],
  );
  const [expandedFunds, setExpandedFunds] = useState<Record<string, boolean>>(
    {},
  );
  const funds = new Map(board.funds.map((f) => [f.key, f]));
  const identity =
    data.dailyResearch?.reportRunId ?? data.dailyResearch?.runId ?? "empty";
  const groupTitles = [
    "🟢 今日优先低吸 · 已入买区",
    "🟡 即将入区 · 重点盯盘",
    "📈 右侧趋势走强观察",
    "🟠 高位止盈参考 · 已入／接近卖区",
    "⚪ 未入区 · 观望等待池",
  ];
  const groups = groupTitles.map((_, rank) =>
    board.funds.filter((fund) => fund.priority.rank === rank),
  );
  function changeExpansion(key: string, value: boolean) {
    setExpandedFunds((previous) => ({
      ...previous,
      [`${identity}:${key}`]: value,
    }));
  }
  function revealFund(key: string, expand = false) {
    const target = document.getElementById(fundLink(key));
    const waiting = target?.closest<HTMLDetailsElement>(".op-today-waiting");
    if (waiting) waiting.open = true;
    if (expand) changeExpansion(key, true);
    target?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }
  const cards = (funds: TodayFund[]) =>
    funds.map((fund) => (
      <FundRow
        key={`${identity}:${fund.key}`}
        fund={fund}
        expanded={
          expandedFunds[`${identity}:${fund.key}`] ?? fund.priority.expanded
        }
        onExpand={(value) => changeExpansion(fund.key, value)}
        frozenNavHistories={frozenNavHistories}
      />
    ));
  return (
    <>
      <OperationQuickTable
        rows={board.funds
          .filter(({ priority }) => priority.tone !== "neutral")
          .map((fund) => ({
            id: fund.key,
            code: fund.code,
            name: fund.name,
            status: fund.priority.label,
            tone: fund.priority.tone,
            plan: fund.plans[0],
            holdingReturnPct: frozenNavHistories
              ? null
              : data.sellRiskAlerts?.holdingReturnPcts?.[fund.code],
            advice: todayFundReading(fund).advice,
          }))}
        onSelect={(key) => revealFund(key, true)}
      />
      <MacroCycleOverview
        data={data}
        historical={frozenNavHistories !== undefined}
      />
      <button
        type="button"
        className="op-workspace-link"
        onClick={onViewEvidence}
      >
        查看完整研判与市场证据
      </button>
      <ObservationSection
        title="基金机会 · 操作优先"
        className="op-today-opportunities"
      >
        <p className="op-today-guide">
          净值进入参考买区后，优先显示持仓与自选，再按行业顺风和近两年净值分位排序。
        </p>
        {board.holdingAlerts.length > 0 && (
          <p className="op-today-holding-alerts" aria-label="持仓操作位置提醒">
            {board.holdingAlerts.map((fund) => (
              <span key={fund.key}>
                {fund.priority.label} ·{" "}
                <FundLink fund={fund} reveal={revealFund} />
              </span>
            ))}
          </p>
        )}
        <section aria-label="按操作紧迫度排序的基金机会">
          {groups.slice(0, 4).map(
            (members, index) =>
              members.length > 0 && (
                <section
                  key={index}
                  className="op-today-group"
                  aria-label={groupTitles[index]}
                >
                  <h3>
                    {groupTitles[index]} · {members.length} 只
                  </h3>
                  {cards(members)}
                </section>
              ),
          )}
          {groups[4].length + board.unpriced.length > 0 && (
            <details className="op-today-waiting" key={identity}>
              <summary>
                {groupTitles[4]} · {groups[4].length + board.unpriced.length} 只
              </summary>
              {cards(groups[4])}
              {board.unpriced.map((fund) => (
                <div className="op-today-unpriced" key={fund.fundCode}>
                  <strong>
                    {fund.name ?? fund.label ?? "基金"} {fund.fundCode}
                  </strong>
                  <span>净值资料待补齐</span>
                </div>
              ))}
            </details>
          )}
        </section>
        <section aria-label="全部研究洞察" className="op-today-insights">
          <details className="op-today-insights-list" key={identity}>
            <summary>研究方向与主题 · {board.insights.length} 项</summary>
            {board.insights.map((insight) => (
              <details
                key={`${identity}:${insight.key}`}
                className="op-today-insight"
              >
                <summary>
                  <small>{insight.kind}</small>
                  <strong>{insight.title}</strong>
                  <span>{insight.priority.label}</span>
                </summary>
                <p>{insight.thesis}</p>
                {insight.auditStatus === "UNAUDITED_SCOPE" && (
                  <small className="op-match-badge">基于已有资讯研判</small>
                )}
                <div className="op-today-related">
                  {insight.funds.map((key) => (
                    <FundLink
                      key={key}
                      fund={funds.get(key)!}
                      reveal={revealFund}
                    />
                  ))}
                </div>
              </details>
            ))}
          </details>
        </section>
      </ObservationSection>
    </>
  );
}

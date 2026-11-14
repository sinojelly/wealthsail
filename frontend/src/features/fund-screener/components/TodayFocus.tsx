import { useMemo, useState } from "react";
import type { FundNavHistory } from "../../../api/funds";
import type {
  ActionableZoneResult,
  OpportunityContext,
} from "../../../api/investment";
import {
  buildTodayBoard,
  hasTodayRightTrend,
  type TodayFund,
} from "../today-action-board";
import { fundDisplayName, planPrice } from "../plan-display";
import { finitePrice, referenceTrack } from "../quantitative-position";
import { sourceHref } from "../macro-display";
import ObservationSection from "./ObservationSection";
import OpportunityFundReference from "./OpportunityFundReference";
import QuantitativePricePanel from "./QuantitativePricePanel";
import MacroCycleOverview from "./MacroCycleOverview";
import "../today-focus.css";

function fundLink(key: string) {
  return `today-fund-${encodeURIComponent(key)}`;
}

function FundLink({ fund }: { fund: TodayFund }) {
  const id = fundLink(fund.key);
  return (
    <a
      href={`#${encodeURIComponent(id)}`}
      onClick={() => {
        const target = document.getElementById(id);
        if (!target) return;
        const alternatives = target.parentElement?.closest<HTMLDetailsElement>(
          "details.op-today-track-alternatives",
        );
        if (alternatives) alternatives.open = true;
      }}
    >
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
  if (!hasTodayRightTrend(plan)) return null;
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
  frozenNavHistories,
}: {
  fund: TodayFund;
  frozenNavHistories?: Record<string, FundNavHistory | null>;
}) {
  const [manualExpansion, setManualExpansion] = useState<boolean>();
  const expanded = manualExpansion ?? fund.priority.expanded;
  const plan = fund.plans[0];
  const track = referenceTrack(plan);
  const macro = plan.macroOpportunity;
  const sources = macro?.sourceUris?.map(sourceHref).filter(Boolean) ?? [];
  return (
    <details
      className="op-today-fund"
      id={fundLink(fund.key)}
      data-testid="today-fund-row"
      data-fund-code={fund.code}
      open={expanded}
      onToggle={(e) => {
        if (e.currentTarget.open !== expanded)
          setManualExpansion(e.currentTarget.open);
      }}
    >
      <summary
        onClick={(e) => {
          e.preventDefault();
          setManualExpansion(!expanded);
        }}
      >
        <span className="op-position-badge" data-tone={fund.priority.tone}>
          {fund.priority.label}
        </span>
        <strong>
          {fundDisplayName(fund.name, fund.code)} {fund.code}
        </strong>
        <span className="op-today-summary-prices">
          净值 {planPrice(plan.currentPrice, plan)} · 买区{" "}
          {planPrice(track?.entryZoneMin, plan)}–
          {planPrice(track?.entryZoneMax, plan)}
        </span>
        {macro && (
          <span className="op-today-fund-evidence">
            <b
              className="op-position-badge"
              data-tone={macro.stance === "TAILWIND" ? "buy" : "near"}
            >
              {macro.label}
            </b>
            <span>{macro.rationale}</span>
          </span>
        )}
        <RightTrendEvidence plan={plan} />
      </summary>
      {expanded && (
        <div className="op-today-fund-body">
          <p className="op-today-origins">
            关联洞察：{fund.origins.join(" · ")}
            {sources.map((source, index) => (
              <a key={source} href={source} target="_blank" rel="noreferrer">
                顺逆风依据 {index + 1}
              </a>
            ))}
          </p>
          {fund.insights.map((insight, index) => (
            <p className="op-today-fund-thesis" key={index}>
              <strong>{insight.title}</strong> {insight.thesis}
            </p>
          ))}
          <OpportunityFundReference
            plan={plan}
            name={fund.name}
            frozenNavHistories={frozenNavHistories}
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
  const funds = new Map(board.funds.map((f) => [f.key, f]));
  const byRepresentative = new Map(
    board.groups.map((group) => [group.representative.key, group]),
  );
  const visible = [
    ...board.funds.filter((fund) => fund.priority.tone !== "neutral"),
    ...board.groups
      .map((group) => group.representative)
      .filter((fund) => fund.priority.tone === "neutral"),
  ];
  const identity =
    data.dailyResearch?.reportRunId ?? data.dailyResearch?.runId ?? "empty";
  return (
    <>
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
          已入买卖区、距买区小于 2%
          或触发趋势与止损观察的基金逐只展示。已入买区优先显示持仓与自选，其次按宏观顺风和近两年净值分位排序。
        </p>
        {board.holdingAlerts.length > 0 && (
          <p className="op-today-holding-alerts" aria-label="持仓操作位置提醒">
            {board.holdingAlerts.map((fund) => (
              <span key={fund.key}>
                {fund.priority.label} · <FundLink fund={fund} />
              </span>
            ))}
          </p>
        )}
        <section aria-label="按操作紧迫度排序的基金机会">
          {visible.map((fund) => {
            const alternatives =
              byRepresentative
                .get(fund.key)
                ?.alternatives.filter(
                  (item) => item.priority.tone === "neutral",
                ) ?? [];
            return (
              <div className="op-today-track" key={`${identity}:${fund.key}`}>
                <FundRow fund={fund} frozenNavHistories={frozenNavHistories} />
                {alternatives.length > 0 && (
                  <details className="op-today-track-alternatives">
                    <summary>
                      同赛道备选基金（{alternatives.length} 只）
                      <span>
                        {alternatives.map((fund) => fund.code).join(" · ")}
                      </span>
                    </summary>
                    {alternatives.map((fund) => (
                      <FundRow
                        key={fund.key}
                        fund={fund}
                        frozenNavHistories={frozenNavHistories}
                      />
                    ))}
                  </details>
                )}
              </div>
            );
          })}
          {board.unpriced.map((fund) => (
            <div className="op-today-unpriced" key={fund.fundCode}>
              <strong>
                {fund.name ?? fund.label ?? "基金"} {fund.fundCode}
              </strong>
              <span>净值资料待补齐</span>
            </div>
          ))}
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
                    <FundLink key={key} fund={funds.get(key)!} />
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

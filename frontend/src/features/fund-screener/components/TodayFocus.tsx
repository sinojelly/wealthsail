import { useMemo, useState } from "react";
import type { FundNavHistory } from "../../../api/funds";
import type { OpportunityContext } from "../../../api/investment";
import { buildTodayBoard, type TodayFund } from "../today-action-board";
import { fundDisplayName, planPrice } from "../plan-display";
import { referenceTrack } from "../quantitative-position";
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
        <span
          className="op-position-badge"
          data-tone={fund.priority.rank < 2 ? "buy" : "neutral"}
        >
          {fund.priority.label}
        </span>
        <strong>
          {fundDisplayName(fund.name, fund.code)} {fund.code}
        </strong>
        <span>
          净值 {planPrice(plan.currentPrice, plan)} · 买区{" "}
          {planPrice(track?.entryZoneMin, plan)}–
          {planPrice(track?.entryZoneMax, plan)}
        </span>
      </summary>
      {expanded && (
        <div className="op-today-fund-body">
          <p className="op-today-origins">
            关联洞察：{fund.origins.join(" · ")}
          </p>
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
          每条赛道选一只代表基金。已入买区或距买区小于
          1.5%，且已核对最新净值和至少一年历史的代表基金，默认最多展开 8 只。
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
          {board.groups.map(({ representative, alternatives }) => (
            <div
              className="op-today-track"
              key={`${identity}:${representative.key}`}
            >
              <FundRow
                key={representative.key}
                fund={representative}
                frozenNavHistories={frozenNavHistories}
              />
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
          ))}
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

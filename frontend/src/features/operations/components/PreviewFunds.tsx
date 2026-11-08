import { useState } from "react";
import { frozenNavHistory, previewMissingReason } from "../preview-data";
import type { PreviewFund } from "../../../api/research-preview";
import type {
  DailyResearch,
  PlanTrack,
  ValueDirection,
} from "../../../api/investment";
import { ActionablePlanEvidence } from "../../fund-screener/components/ActionablePlan";
import ViewportFundChart from "../../fund-screener/components/ViewportFundChart";
import {
  fundDisplayName,
  planPrice,
  planUnit,
} from "../../fund-screener/plan-display";
import { sourceHref, timeLabel } from "../../fund-screener/macro-display";
import { IndustryCycleWarning } from "../../fund-screener/components/IndustryCycle";

const qualificationReasons: Record<string, string> = {
  DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED:
    "行业核查尚缺可信执行凭证或充分原文证据",
  DIRECTION_PLAN_SECTOR_UNREVIEWED: "基金所属行业尚未完成独立核查",
  FUND_PLAN_INVALID: "原基金计划或价格依据未通过严格核验",
  OBSERVATION_ONLY: "仅保留研究观察，不具备投资资格",
  HISTORICAL_SIMULATION: "历史模拟不授予当前操作资格",
};

export function PreviewFundCard({
  fund,
  validatedAt,
  directions = [],
  sources = [],
}: {
  fund: PreviewFund;
  validatedAt: string;
  directions?: ValueDirection[];
  sources?: NonNullable<DailyResearch["report"]>["sources"];
}) {
  const [selected, setSelected] = useState<PlanTrack>(
    fund.originalProposals.some((row) => row.track === "RIGHT_TREND")
      ? "RIGHT_TREND"
      : "LEFT_VALUE",
  );
  const anchor = fund.anchor;
  const track = anchor?.tracks.find((row) => row.track === selected);
  const code = fund.fundCode ?? fund.subjectId;
  const price = (value: string | number | null | undefined) =>
    anchor ? planPrice(value, anchor) : "—";
  const history = frozenNavHistory(fund, validatedAt);
  const linkedDirections = directions.filter((direction) =>
    fund.directionKeys.includes(direction.key),
  );
  return (
    <article
      className="op-value-fund research-preview-fund"
      aria-label={`历史基金 ${code}`}
    >
      <header>
        <h3>
          {fundDisplayName(fund.label, fund.fundCode)}{" "}
          <span>{fund.fundCode ?? "基金代码未保存"}</span>
        </h3>
        <span className="op-value-signal signal-watch">
          历史只读 · 不授予投资资格
        </span>
      </header>
      <p>
        原始研究提及 ·{" "}
        {fund.directionKeys.length
          ? `关联方向 ${fund.directionKeys.join("、")}`
          : "独立基金观察"}
      </p>
      <section
        aria-label={`${code}研究资格与风险`}
        className="research-preview-risk"
      >
        <strong>资格状态：仅供历史观察 · 不授予投资资格</strong>
        {!!fund.formalEligibilityReasons?.length && (
          <ul>
            {fund.formalEligibilityReasons.map((reason) => (
              <li key={reason}>
                {qualificationReasons[reason] ?? "原研究的操作资格未放行"}{" "}
                <small>（{reason}）</small>
              </li>
            ))}
          </ul>
        )}
        <IndustryCycleWarning
          cycle={anchor?.industryCycle}
          warning={anchor?.industryRiskWarning}
          sources={sources}
        />
        {linkedDirections.flatMap((direction) =>
          (direction.risks ?? []).map((risk, index) => (
            <p key={`${direction.key}:${index}`}>
              <strong>{direction.title} · 风险：</strong>
              {risk.summary}
              {risk.sourceIds.map((id) => {
                const source = sources.find((row) => row.id === id);
                const href = sourceHref(source?.url);
                return source && href ? (
                  <a key={id} href={href} target="_blank" rel="noreferrer">
                    {" "}
                    [{source.title}]
                  </a>
                ) : null;
              })}
            </p>
          )),
        )}
        {!linkedDirections.some((direction) => direction.risks?.length) &&
          !anchor?.industryCycle && (
            <p>冻结材料未保存可关联的行业风险结论；资料缺失不表示没有风险。</p>
          )}
      </section>
      <div className="op-value-fund-layout">
        <div className="op-value-price-column">
          <p className="op-value-current">
            研究时确认净值：<strong>{price(anchor?.currentPrice)}</strong>{" "}
            {anchor ? planUnit(anchor.unit) : "—"}{" "}
            <small>{timeLabel(anchor?.asOf)}</small>
          </p>
          <p className="research-preview-muted">
            冻结价格口径：{anchor?.priceBasis ?? "缺少价格口径"}
            ；当前净值未读取。
          </p>
          <div
            className="research-preview-tracks"
            role="group"
            aria-label={`${code}历史轨道`}
          >
            {(
              [
                ["LEFT_VALUE", "左侧低吸"],
                ["RIGHT_TREND", "右侧趋势"],
              ] as const
            ).map(([key, label]) => (
              <button
                type="button"
                key={key}
                aria-pressed={selected === key}
                onClick={() => setSelected(key)}
              >
                {label}
              </button>
            ))}
          </div>
          {track ? (
            <>
              <p>
                研究时轨道状态：
                {{
                  CANDIDATE: "候选条件成立",
                  WATCH: "等待触发",
                  INSUFFICIENT: "证据不足",
                }[track.state] ?? "待核验"}
              </p>
              <dl className="op-value-prices">
                <div>
                  <dt>研究时买入区间</dt>
                  <dd>
                    {price(track.entryZoneMin)}–{price(track.entryZoneMax)}
                  </dd>
                </div>
                <div>
                  <dt>研究时卖出区间</dt>
                  <dd>
                    {price(track.sellZoneMin ?? track.takeProfitT1)}–
                    {price(track.sellZoneMax ?? track.takeProfitT2)}
                  </dd>
                </div>
                <div>
                  <dt>止损参考</dt>
                  <dd>{price(track.stopLossPrice)}</dd>
                </div>
                <div>
                  <dt>止盈 T1 / T2</dt>
                  <dd>
                    {price(track.takeProfitT1)} / {price(track.takeProfitT2)}
                  </dd>
                </div>
              </dl>
              {track.missingEvidence?.length ? (
                <p role="status">
                  缺少依据：{track.missingEvidence.join("；")}
                </p>
              ) : null}
            </>
          ) : (
            <p role="status">原材料未保存该轨道的价格区间</p>
          )}
          {fund.originalProposals.map((proposal, index) => (
            <p
              key={`${proposal.directionKey}:${index}`}
              className="research-preview-muted"
            >
              {!proposal.track && !proposal.note
                ? "原研究基金清单提及"
                : `原始计划：${proposal.track === "LEFT_VALUE" ? "左侧低吸" : proposal.track === "RIGHT_TREND" ? "右侧趋势" : "轨道待核验"}`}
              {proposal.note ? ` · ${proposal.note}` : ""}
            </p>
          ))}
          <div className="research-preview-actions">
            <button type="button" disabled title="历史模拟不能保存跟踪">
              跟踪研究 · {code}
            </button>
            <button type="button" disabled title="历史模拟不能创建提醒">
              设置提醒 · {code}
            </button>
          </div>
          <small>
            动作已禁用：历史模拟只供核验，不生成跟踪、提醒或交易指令。
          </small>
          {anchor && (
            <details>
              <summary>基金详情与价格依据</summary>
              <p>算法版本：{anchor.methodVersion}</p>
              <p>价格证据日期：{timeLabel(anchor.asOf)}</p>
              {sourceHref(anchor.sourceUri) && (
                <a
                  href={sourceHref(anchor.sourceUri)}
                  target="_blank"
                  rel="noreferrer"
                >
                  原价格来源
                </a>
              )}
              <ActionablePlanEvidence
                result={{ ...anchor, tracks: track ? [track] : anchor.tracks }}
              />
            </details>
          )}
        </div>
        <div className="research-preview-history">
          {fund.fundCode && (
            <ViewportFundChart
              fundCode={fund.fundCode}
              track={track}
              confirmedNavAsOf={anchor?.asOf ?? undefined}
              snapshot={history}
            />
          )}
          {!history && (
            <p role="status">
              冻结材料未保存净值历史；不能用摘要或分位数补画曲线。
            </p>
          )}
          {!!fund.missingReasons.length && (
            <ul aria-label={`${code}历史资料缺口`}>
              {fund.missingReasons.map((reason) => (
                <li key={reason}>
                  {previewMissingReason(reason)} <small>（{reason}）</small>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </article>
  );
}

export default function PreviewFunds({
  funds,
  validatedAt,
  directions,
  sources,
}: {
  funds: PreviewFund[];
  validatedAt: string;
  directions?: ValueDirection[];
  sources?: NonNullable<DailyResearch["report"]>["sources"];
}) {
  return (
    <section
      className="research-preview-funds"
      aria-label="原始研究全部提及基金"
    >
      <header>
        <h2>原始研究全部提及基金</h2>
        <p>
          共 {funds.length}{" "}
          只。正式计划被剥离的基金仍保留原时点资料；这里不读取当前持仓或实时行情。
        </p>
      </header>
      {funds.length ? (
        funds.map((fund) => (
          <PreviewFundCard
            key={fund.key}
            fund={fund}
            validatedAt={validatedAt}
            directions={directions}
            sources={sources}
          />
        ))
      ) : (
        <p>原始材料没有可识别的基金记录。</p>
      )}
    </section>
  );
}

import { useState } from "react";
import type { PersonalFundReference } from "../../../api/personal-funds";
import type { PlanTrack } from "../../../api/investment";
import { FundWatchQuote } from "./PriceWatchSummary";
import ViewportFundChart from "./ViewportFundChart";
import { planPrice, planUnit } from "../plan-display";
import { sourceHref, timeLabel } from "../macro-display";
import { ActionablePlanEvidence } from "./ActionablePlan";
import { relationshipLabels } from "../personal-fund-model";

const missingLabels: Record<string, string> = {
  LOCAL_NAV_HISTORY_UNAVAILABLE: "本地尚无可核验净值历史",
  LOCAL_PRICE_PLAN_INCOMPLETE: "本地净值历史或连续性不足，价格轨道仅供观察",
  LOCAL_ESTIMATE_UNAVAILABLE: "本地暂无当日同基金估值，未触发外部采集",
  LOCAL_ESTIMATE_SOURCE_UNVERIFIED:
    "本地存在估值记录，但未保存可核验的原来源证明，未展示为可靠估值",
  LOCAL_NAV_IDENTITY_UNVERIFIED: "本地人民币基金身份尚未核实，未混用同代码净值",
  LOCAL_NAV_MARKET_UNSUPPORTED: "当前市场尚未接入对应单位净值资料",
};

/** Only supplied local facts; this component never subscribes to live APIs. */
export default function FundReferenceDetails({
  fund,
  historical = false,
}: {
  fund: PersonalFundReference;
  historical?: boolean;
}) {
  const [selected, setSelected] = useState<PlanTrack>("LEFT_VALUE");
  const plan = fund.plan;
  const track = plan?.tracks.find((row) => row.track === selected);
  const price = (value: string | number | null | undefined) =>
    plan ? planPrice(value, plan) : "—";
  return (
    <section
      className="op-current-fund-reference"
      aria-label={`${fund.fundCode ?? fund.label}当前价格参考`}
    >
      <strong>当前本地价格参考 · 仅供观察</strong>
      <p>
        {fund.relationships.map((key) => relationshipLabels[key]).join(" · ")}
        ；程序价位不授予投资资格。
      </p>
      <FundWatchQuote row={fund.confirmedNav} label="本地最新确认净值" />
      <FundWatchQuote
        row={fund.currentEstimate}
        label="本地当日估算净值（非最终净值）"
        estimate
      />
      {fund.currentEstimate?.sourceVerification === "LOCAL_RECORD_ONLY" && (
        <p role="note">
          本地Worker记录，原公开来源未保存/待核；此估算不用于价格计划、触发判断或投资资格。
        </p>
      )}
      <div
        className="research-preview-tracks"
        role="group"
        aria-label={`${fund.fundCode}当前轨道`}
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
            当前程序轨道：
            {track.state === "CANDIDATE"
              ? "候选"
              : track.state === "WATCH"
                ? "观察"
                : "资料不足"}{" "}
            · {timeLabel(plan!.asOf)} · {planUnit(plan!.unit)}
          </p>
          <dl className="op-value-prices">
            <div>
              <dt>当前买入区间参考</dt>
              <dd>
                {price(track.entryZoneMin)}–{price(track.entryZoneMax)}
              </dd>
            </div>
            <div>
              <dt>当前卖出区间参考</dt>
              <dd>
                {price(track.sellZoneMin ?? track.takeProfitT1)}–
                {price(track.sellZoneMax ?? track.takeProfitT2)}
              </dd>
            </div>
            <div>
              <dt>当前止损参考</dt>
              <dd>{price(track.stopLossPrice)}</dd>
            </div>
            <div>
              <dt>当前止盈 T1 / T2</dt>
              <dd>
                {price(track.takeProfitT1)} / {price(track.takeProfitT2)}
              </dd>
            </div>
          </dl>
          <details>
            <summary>价格依据与风险说明</summary>
            <ActionablePlanEvidence result={plan!} />
          </details>
        </>
      ) : (
        <p>本地资料尚不能形成完整双轨价格区间。</p>
      )}
      {fund.fundMarket === "FUND_CN" && fund.fundCode ? (
        <ViewportFundChart
          fundCode={fund.fundCode}
          snapshot={fund.history}
          snapshotMode="CURRENT_LOCAL"
          track={track}
          confirmedNavAsOf={fund.confirmedNav?.asOf ?? undefined}
        />
      ) : (
        <p>该市场尚无对应单位净值曲线，未混用其他市场价格。</p>
      )}
      {fund.history?.sourceUri && sourceHref(fund.history.sourceUri) && (
        <small>
          <a
            href={sourceHref(fund.history.sourceUri)}
            target="_blank"
            rel="noreferrer"
          >
            净值原来源
          </a>{" "}
          · 获取于{timeLabel(fund.history.retrievedAt)}
        </small>
      )}
      {fund.comments?.map((comment, index) => (
        <aside key={index}>
          <strong>原研究点评：{comment.title}</strong>
          <p>{comment.thesis}</p>
          {comment.risks.map((risk, i) => (
            <p key={i}>风险：{risk.summary}</p>
          ))}
        </aside>
      ))}
      {!!fund.missingReasons.length && (
        <ul>
          {fund.missingReasons.map((reason) => (
            <li key={reason}>{missingLabels[reason] ?? reason}</li>
          ))}
        </ul>
      )}
      {historical && (
        <div className="research-preview-actions">
          <button disabled title="历史页禁止保存跟踪">
            保存当前跟踪
          </button>
          <button disabled title="历史页禁止创建提醒">
            设置当前提醒
          </button>
        </div>
      )}
    </section>
  );
}

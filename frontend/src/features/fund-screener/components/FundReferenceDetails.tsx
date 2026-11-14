import type { PersonalFundReference } from "../../../api/personal-funds";
import { FundWatchQuote } from "./PriceWatchSummary";
import ViewportFundChart from "./ViewportFundChart";
import { sourceHref, timeLabel } from "../macro-display";
import { referenceTrack } from "../quantitative-position";
import { relationshipLabels } from "../personal-fund-model";
import FundPriceTracks from "./FundPriceTracks";

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
  const plan = fund.plan;
  const track = plan ? referenceTrack(plan) : undefined;
  return (
    <section
      className="op-current-fund-reference"
      aria-label={`${fund.fundCode ?? fund.label}当前价格参考`}
    >
      <strong>当前价格参考</strong>
      <small>
        {fund.relationships.map((key) => relationshipLabels[key]).join(" · ")}
      </small>
      <div className="op-value-fund-layout">
        <div className="op-value-price-column">
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
          {plan ? (
            <>
              <FundPriceTracks plan={plan} basis="当前" />
            </>
          ) : (
            <p>正在补齐参考买卖点所需的净值资料。</p>
          )}
        </div>
        <div className="op-my-fund-chart">
          {fund.fundMarket === "FUND_CN" && fund.fundCode ? (
            <ViewportFundChart
              fundCode={fund.fundCode}
              immediate
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
        </div>
      </div>
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

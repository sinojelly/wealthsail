import FundNavTrendChart from "./FundNavTrendChart";
import { buyZoneStatus, buyZoneClassName } from "../opportunity-display";
import type {
  ActionableZoneResult,
  DailyResearch,
  PlanPrice,
  PlanTrack,
} from "../../../api/investment";
import { sourceHref, timeLabel } from "../macro-display";
import {
  fundDisplayName,
  planPrice,
  planTimeBasisNote,
  planUnit,
} from "../plan-display";
import { readingText } from "../research-reading";
import PlanRevision from "./PlanRevision";
import EstimatedProfitRange from "./EstimatedProfitRange";
import PlanPriceRefresh from "./PlanPriceRefresh";
import { IndustryCycleBadge, IndustryCycleWarning } from "./IndustryCycle";
import QuantitativePricePanel from "./QuantitativePricePanel";
import { referenceTrack } from "../quantitative-position";

const planTrackLabel = (track: PlanTrack) =>
  track === "LEFT_VALUE" ? "左侧低吸" : "右侧趋势";
const states = {
  CANDIDATE: "候选条件成立",
  WATCH: "等待触发",
  INSUFFICIENT: "条件数据不足",
};
const parameterLabel = (key: string) =>
  (
    ({
      ma: "均线周期",
      rsi: "相对强弱周期",
      atr: "真实波幅周期",
      bollinger: "布林周期与倍数",
      momentum: "动量周期",
      maximumPePercentile: "市盈率分位上限",
      maximumMA200Gap: "偏离200期均线上限",
      oversoldRSI: "超卖强弱阈值",
      bollingerProximity: "布林接近倍数",
      maximumTrendRSI: "趋势强弱上限",
      navPercentileWindow: "净值分位观察样本数",
      maximumNavPercentile: "高位净值分位阈值",
      maximumHighNavMA20Ratio: "高位净值与20期均线比值上限",
      trendEntryLowScale: "趋势买区下沿波动倍数",
      trendEntryHighScale: "趋势买区上沿波动倍数",
      navAbsoluteChangeWindow: "净值绝对变化窗口",
      navAbsoluteChangeMultiplier: "净值绝对变化倍数",
      navStdWindow: "净值标准差窗口",
      navStdMultiplier: "净值标准差倍数",
      navRiskFloorRatio: "净值最小风险幅度",
      minimumRewardRiskT1: "目标一最低风险收益比",
      minimumRewardRiskT2: "目标二最低风险收益比",
      rewardRiskBasis: "风险基准",
      historicalPriceWindow: "历史价格样本窗口",
      historicalPercentileMethod: "历史价格分位算法",
      middleHighNavPercentile: "中高位净值分位阈值",
      middleHighPriceSpanRatio: "中高位周期跨度比例阈值",
      maximumHighBuyPricePercentile: "中高位买区历史分位上限",
      maximumLowBuyPricePercentile: "中低位买区历史分位上限",
      maximumHighBuyPriceSpanRatio: "中高位买区周期跨度比例上限",
      maximumLowBuyPriceSpanRatio: "中低位买区周期跨度比例上限",
      maximumTrendBuyPricePercentile: "趋势买区历史价格分位上限",
      sellZoneLowerPricePercentile: "卖区下沿历史价格分位",
      sellZoneUpperPricePercentile: "卖区上沿历史价格分位",
      minimumSellPriceSpanRatio: "卖区下沿周期跨度比例下限",
      minimumTargetProfitRatio: "最低目标价差比例",
      historicalPeakSellCapRatio: "历史高点卖区封顶比例",
      navRewardRiskBasis: "净值买卖区间依据",
    }) as Record<string, string>
  )[key] ?? "其他策略参数";

const coordinateLabel = (result: ActionableZoneResult) =>
  result.subjectKind === "index" || result.priceBasis === "INDEX_PRICE"
    ? "指数点仅为指数研究坐标，不可作为基金挂单价格。"
    : result.priceBasis === "ETF_HFQ" || result.priceBasis === "ADJUSTED_PRICE"
      ? "后复权研究坐标，不可直接作为实时挂单价格。"
      : result.priceBasis.includes("NAV")
        ? "净值研究坐标，不可作为实时挂单价格。"
        : "同口径研究坐标，价格口径未确认时不可直接挂单。";

/** Shared evidence only; the owning plan renders its price table once. */
export function ActionablePlanEvidence({
  result,
  showConditions = true,
}: {
  result: ActionableZoneResult;
  showConditions?: boolean;
}) {
  const indicatorLabels: Record<string, string> = {
    ma20: "20期均线",
    ma60: "60期均线",
    ma200: "200期均线",
    rsi14: "14期相对强弱",
    atr14: "真实ATR14",
    navVolatility14: "14期净值波动尺度",
    bollUpper: "布林上轨",
    bollMiddle: "布林中轨",
    bollLower: "布林下轨",
    momentum20: "20期动量",
    navPercentile1y: "近1年净值分位（%）",
    p30: "历史30%分位价格",
    p35: "历史35%分位价格",
    p40: "历史40%分位价格",
    p45: "历史45%分位价格",
    p50: "历史中位价格",
    p55: "历史55%分位价格",
    p60: "历史60%分位价格",
    p65: "历史65%分位价格",
    p75: "历史75%分位价格",
    p85: "历史85%分位价格",
    p92: "历史92%分位价格",
    p95: "历史95%分位价格",
    troughLow: "历史周期低点",
    peakHigh: "历史周期高点",
    priceSpan: "历史周期高低跨度",
  };
  const numeric = (value: number | string | null, digits = 4) =>
    value == null || !Number.isFinite(Number(value))
      ? "—"
      : Number(value).toFixed(digits);
  const parameter = (value: number | string) =>
    typeof value === "string"
      ? ({
          DISPLAYED_ENTRY_MID_AND_STOP: "显示买区中点与止损",
          LINEAR_INTERPOLATION: "线性插值",
          HISTORICAL_SUPPORT_RESISTANCE_NO_RR_FLOOR:
            "历史支撑与阻力，不按现价抬高目标",
          TWO_YEAR_SPAN_SUPPORT_RESISTANCE_PEAK_CAPPED:
            "近2年高低跨度支撑与阻力，卖区受历史高点约束",
        }[value] ?? readingText(value, 100))
      : Number.isFinite(value)
        ? String(Number(value.toFixed(4)))
        : "—";
  return (
    <div className="op-plan-evidence">
      {planTimeBasisNote(result) && <p>{planTimeBasisNote(result)}</p>}
      {result.industryCycle?.evidenceSummary && (
        <p>
          产业阶段依据：{readingText(result.industryCycle.evidenceSummary, 400)}
        </p>
      )}
      {result.relativeStrength && (
        <section className="op-relative-strength" aria-label="板块相对强弱依据">
          <p>
            <strong>
              相对{result.relativeStrength.benchmark ?? "沪深300"}的板块强弱
            </strong>
          </p>
          {result.relativeStrength.weakening === true ? (
            <p className="op-relative-strength-warning" role="status">
              📉 相对沪深300持续走弱（已触发趋势风控）
            </p>
          ) : result.relativeStrength.trendWeakening === true ? (
            <p className="op-relative-strength-warning" role="status">
              📉 反弹高点下移，趋势风险仍需观察。
            </p>
          ) : null}
          <p>
            20日超额收益：{numeric(result.relativeStrength.excessReturn20d, 2)}
            {result.relativeStrength.excessReturn20d != null ? " 个百分点" : ""}
          </p>
          <p>
            60日超额收益：{numeric(result.relativeStrength.excessReturn60d, 2)}
            {result.relativeStrength.excessReturn60d != null ? " 个百分点" : ""}
          </p>
          <p>
            近60日高点回落：{numeric(result.relativeStrength.drawdown60d, 2)}
            {result.relativeStrength.drawdown60d != null ? "%" : ""}
          </p>
          <p>
            本轮走弱分水岭（跌破MA60）：
            {result.relativeStrength.breakdownDate
              ? timeLabel(result.relativeStrength.breakdownDate)
              : "尚未确认"}
          </p>
          {result.relativeStrength.missingEvidence?.length ? (
            <p>
              待补充：
              {readingText(
                result.relativeStrength.missingEvidence.join("；"),
                400,
              )}
            </p>
          ) : null}
        </section>
      )}
      {showConditions && (
        <>
          <p>{coordinateLabel(result)}</p>
          {result.volatilityMethod === "NAV_ABS_CHANGE_STD_V1" && (
            <p>
              净值波动风险法：使用真实净值变化与标准差计算风险尺度，区别于真实高低价ATR。
            </p>
          )}
          {result.tracks.map((track) => (
            <section
              key={track.track}
              aria-label={`${planTrackLabel(track.track)}风险条件`}
            >
              <p>
                {planTrackLabel(track.track)} · {states[track.state]}
              </p>
              {track.buyZoneAnchor && (
                <p>
                  买区锚点：
                  {
                    {
                      MA60_HISTORICAL_SUPPORT: "60期均线与历史中位支撑区",
                      MA20_CAPPED_P65: "20期均线，受历史65%分位价格约束",
                      SHORT_HISTORY_REFERENCE: "短期历史参考，长期样本待补齐",
                      LEFT_BOLLINGER_SUPPORT: "布林下轨支撑与历史价格上限",
                      TWO_YEAR_LOWER_SUPPORT:
                        "近2年下半区支撑带与60期、200期均线",
                      TWO_YEAR_LOW_SUPPORT: "当前低位与近2年下半区支撑带",
                    }[track.buyZoneAnchor]
                  }
                </p>
              )}
              {track.priceState === "PARTIAL" && (
                <p>部分价格依据：缺少完整输入，止损或目标可能尚未计算。</p>
              )}
              {track.reasons.map((reason, index) => (
                <p key={index}>{readingText(reason, 600)}</p>
              ))}
              {!!track.missingEvidence?.length && (
                <p>
                  待补充：{readingText(track.missingEvidence.join("；"), 600)}
                </p>
              )}
            </section>
          ))}
        </>
      )}
      {!!result.calibrationRuleKeys?.length && (
        <p>结构化复盘规则：{result.calibrationRuleKeys.join("、")}</p>
      )}
      <p>
        方法：{result.methodVersion} · 价格口径：
        {(
          {
            UNIT_NAV: "单位净值",
            ACCUMULATED_NAV: "累计净值",
            INDEX_PRICE: "指数点位",
            INDEX_POINTS: "指数点位",
            ETF_HFQ: "后复权价格",
            ADJUSTED_PRICE: "复权价格",
            PROVIDER_RETURN_INDEX: "供应商收益指数",
          } as Record<string, string>
        )[result.priceBasis] ?? "待核查口径"}{" "}
        · 标的：{result.subjectId}
      </p>
      {result.parameters && (
        <p>
          参数：
          {Object.entries(result.parameters)
            .map(
              ([key, value]) =>
                `${parameterLabel(key)}=${Array.isArray(value) ? value.map(parameter).join(", ") : parameter(value)}`,
            )
            .join("；")}
        </p>
      )}
      {result.historicalPriceWindow && (
        <p>
          历史价格锚点：{result.historicalPriceWindow.sampleCount}个有效交易样本
          {result.historicalPriceWindow.start &&
          result.historicalPriceWindow.end
            ? `（${timeLabel(result.historicalPriceWindow.start)}至${timeLabel(result.historicalPriceWindow.end)}）`
            : ""}
          {result.historicalPriceWindow.sampleCount <
          result.historicalPriceWindow.requiredSamples
            ? "，样本不足，历史买卖区间依据待补齐。"
            : "。"}
        </p>
      )}
      {Object.entries(result.indicators).map(([name, value]) => (
        <p key={name}>
          {indicatorLabels[name] ?? "其他算法观察"}：
          {numeric(
            value,
            name === "rsi14" || name === "navPercentile1y"
              ? 2
              : result.subjectKind === "index"
                ? 2
                : 4,
          )}
        </p>
      ))}
      {sourceHref(result.sourceUri) && (
        <a href={sourceHref(result.sourceUri)} target="_blank" rel="noreferrer">
          原始价格来源
        </a>
      )}
    </div>
  );
}

export default function ActionablePlan({
  result,
  onTrack,
  pending = false,
  frozen = false,
  sources,
}: {
  result: ActionableZoneResult;
  onTrack?: (track: PlanTrack) => void;
  pending?: boolean;
  frozen?: boolean;
  sources?: NonNullable<DailyResearch["report"]>["sources"];
}) {
  const price = (value: PlanPrice | undefined) => planPrice(value, result);
  if (result.methodVersion.startsWith("actionable-zones-v6")) {
    const track = referenceTrack(result);
    const code = result.fundCode ?? result.subjectId;
    return (
      <section
        className="op-actionable-plan"
        aria-label={`${fundDisplayName(result.label, code)}参考买卖计划`}
      >
        <header className="op-plan-heading">
          <h3>{fundDisplayName(result.label, code)} · 参考买卖计划</h3>
          <IndustryCycleBadge cycle={result.industryCycle} />
        </header>
        <IndustryCycleWarning
          cycle={result.industryCycle}
          warning={result.industryRiskWarning}
          sources={sources}
        />
        <QuantitativePricePanel
          plan={result}
          basis={frozen ? "研究时" : "当前"}
        />
        {onTrack && track && track.state !== "INSUFFICIENT" && (
          <button
            type="button"
            disabled={pending}
            onClick={() => onTrack(track.track)}
          >
            跟踪当前参考计划
          </button>
        )}
        {result.priceBasis === "UNIT_NAV" && /^\d{6}$/.test(code) && (
          <FundNavTrendChart
            fundCode={code}
            track={track}
            confirmedNavAsOf={result.asOf ?? undefined}
          />
        )}
      </section>
    );
  }
  const unit = planUnit(result.unit);
  const selected = result.tracks.find(
    (row) => row.track === (result.selectedTrack ?? result.track),
  );
  const status = selected ? buyZoneStatus(result, selected) : null;
  return (
    <section
      className="op-actionable-plan"
      aria-label={`${fundDisplayName(result.label, result.fundCode ?? result.subjectId)}双轨计划`}
    >
      <header className="op-plan-heading">
        <h3>
          {fundDisplayName(result.label, result.fundCode ?? result.subjectId)} ·
          双轨计划
        </h3>
        <IndustryCycleBadge cycle={result.industryCycle} />
      </header>
      <PlanRevision
        plan={{ ...result, selectedTrack: undefined, track: undefined }}
      />
      {!!result.calibrationRuleKeys?.length && result.calibrationNote && (
        <p>复盘校准：{readingText(result.calibrationNote, 600)}</p>
      )}
      {result.volatilityMethod === "NAV_ABS_CHANGE_STD_V1" && (
        <p>
          净值波动风险法：使用真实净值变化与标准差计算风险尺度，区别于真实高低价ATR。
        </p>
      )}
      <p>
        实际观察：
        <span className="op-price-amount">
          {price(result.currentPrice)} {unit}
        </span>{" "}
        · {timeLabel(result.asOf)}
        {status && (
          <span className={buyZoneClassName(status)}>{status.text}</span>
        )}
      </p>
      <IndustryCycleWarning
        cycle={result.industryCycle}
        warning={result.industryRiskWarning}
        sources={sources}
      />
      <PlanPriceRefresh plan={result} />
      {planTimeBasisNote(result) && (
        <p className="op-plan-time-basis">{planTimeBasisNote(result)}</p>
      )}
      <p>{coordinateLabel(result)}</p>
      {(result.selectedTrack ?? result.track) && (
        <p>
          {frozen ? "冻结轨道" : "研究轨道"}：
          {planTrackLabel((result.selectedTrack ?? result.track)!)}
        </p>
      )}
      <div className="op-plan-tracks">
        {result.tracks.map((track) => (
          <article key={track.track} aria-label={planTrackLabel(track.track)}>
            <h4>
              {planTrackLabel(track.track)}{" "}
              <span className="op-badge">{states[track.state]}</span>
            </h4>
            <dl className="op-value-prices">
              <div>
                <dt>建议买入区间</dt>
                <dd>
                  {price(track.entryZoneMin)}–{price(track.entryZoneMax)} {unit}
                </dd>
              </div>
              <div>
                <dt>建议卖出区间</dt>
                <dd
                  className={
                    result.inSellZone === true || track.inSellZone === true
                      ? "op-sell-zone-active"
                      : undefined
                  }
                >
                  {price(track.sellZoneMin ?? track.takeProfitT1)}–
                  {price(track.sellZoneMax ?? track.takeProfitT2)}
                </dd>
                <small>T1 / T2 分批止盈</small>
              </div>
              <div className="op-value-stop">
                <dt>止损参考</dt>
                <dd>
                  {price(track.stopLossPrice)} {unit}
                </dd>
              </div>
              <EstimatedProfitRange
                track={track}
                priceRefresh={result.priceRefresh}
              />
            </dl>
            {(result.inSellZone === true || track.inSellZone === true) && (
              <p className="op-sell-zone-conclusion">
                {result.priceRefresh && result.priceRefresh.state !== "READY"
                  ? "已触发卖出观察条件；当前买卖区间依据尚未完整，仅供参考，请核对最新净值与价格依据后再评估。"
                  : "当前净值已进入建议卖出区间，持仓可分批止盈兑现；空仓请等待回落至建议买入区间再评估。"}
              </p>
            )}
            {track.priceState === "PARTIAL" && (
              <p>部分价格依据：缺少完整输入，止损或目标可能尚未计算。</p>
            )}
            {track.reasons.map((reason, index) => (
              <p key={index}>{readingText(reason, 600)}</p>
            ))}
            {!!track.missingEvidence?.length && (
              <p>
                待补充：{readingText(track.missingEvidence.join("；"), 600)}
              </p>
            )}
            {onTrack && track.state !== "INSUFFICIENT" && (
              <button
                type="button"
                disabled={pending}
                onClick={() => onTrack(track.track)}
              >
                跟踪{planTrackLabel(track.track)}计划
              </button>
            )}
          </article>
        ))}
      </div>
      {result.priceBasis === "UNIT_NAV" &&
        /^\d{6}$/.test(result.fundCode ?? result.subjectId) && (
          <FundNavTrendChart
            fundCode={result.fundCode ?? result.subjectId}
            track={selected}
          />
        )}
      <details>
        <summary>价格依据与算法</summary>
        <ActionablePlanEvidence result={result} showConditions={false} />
      </details>
    </section>
  );
}

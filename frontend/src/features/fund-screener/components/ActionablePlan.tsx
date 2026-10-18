import type {
  ActionableZoneResult,
  PlanPrice,
  PlanTrack,
} from "../../../api/investment";
import { sourceHref, timeLabel } from "../macro-display";
import { planPrice, planUnit } from "../plan-display";
import { readingText } from "../research-reading";

const planTrackLabel = (track: PlanTrack) =>
  track === "LEFT_VALUE" ? "左侧低吸" : "右侧趋势";
const states = {
  CANDIDATE: "候选条件成立",
  WATCH: "等待触发",
  INSUFFICIENT: "条件数据不足",
};

export default function ActionablePlan({
  result,
  onTrack,
  pending = false,
  frozen = false,
}: {
  result: ActionableZoneResult;
  onTrack?: (track: PlanTrack) => void;
  pending?: boolean;
  frozen?: boolean;
}) {
  const basis = result.priceBasis;
  const price = (value: PlanPrice | undefined) => planPrice(value, result);
  const unit = planUnit(result.unit);
  const indicatorLabels: Record<string, string> = {
    ma20: "20期均线",
    ma60: "60期均线",
    ma200: "200期均线",
    rsi14: "14期相对强弱",
    atr14: "真实ATR14",
    navVolatility14: "14期净值波动尺度",
    bollUpper: "布林上轨",
    bollLower: "布林下轨",
    momentum20: "20期动量",
  };
  const numeric = (value: number | string | null, digits = 4) =>
    value == null || !Number.isFinite(Number(value))
      ? "—"
      : Number(value).toFixed(digits);
  const coordinate =
    result.subjectKind === "index" || basis === "INDEX_PRICE"
      ? "指数点仅为指数研究坐标，不可作为基金挂单价格。"
      : basis === "ETF_HFQ" || basis === "ADJUSTED_PRICE"
        ? "后复权研究坐标，不可直接作为实时挂单价格。"
        : basis.includes("NAV")
          ? "净值研究坐标，不可作为实时挂单价格。"
          : "同口径研究坐标，价格口径未确认时不可直接挂单。";
  return (
    <section
      className="op-actionable-plan"
      aria-label={`${result.label}双轨计划`}
    >
      <h3>{result.label} · 双轨计划</h3>
      {result.priceRevisionStatus && (
        <p>
          计划调整：
          {(
            {
              INITIAL: "初始计划",
              UPGRADED: "上调价格区间",
              DOWNGRADED: "下调价格区间",
              TAKE_PROFIT_ALERT: "止盈复核提示",
            } as Record<string, string>
          )[result.priceRevisionStatus] ?? "调整状态待核对"}
        </p>
      )}
      {result.priceRevisionNote && (
        <p>{readingText(result.priceRevisionNote, 600)}</p>
      )}
      {result.calibrationNote && (
        <p>复盘校准：{readingText(result.calibrationNote, 600)}</p>
      )}
      {result.volatilityMethod === "NAV_ABS_CHANGE_STD_V1" && (
        <p>
          净值波动风险法：使用真实净值变化与标准差计算风险尺度，区别于真实高低价ATR。
        </p>
      )}
      <p>
        实际观察：{price(result.currentPrice)} {unit} · {timeLabel(result.asOf)}
      </p>
      <p>{coordinate}</p>
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
            <p>
              入场区间：{price(track.entryZoneMin)}–{price(track.entryZoneMax)}{" "}
              {unit}
            </p>
            <p>
              止损：{price(track.stopLossPrice)} · 目标一：
              {price(track.takeProfitT1)} · 目标二：{price(track.takeProfitT2)}{" "}
              {unit}
            </p>
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
      <details>
        <summary>价格依据与算法</summary>
        {!!result.calibrationRuleKeys?.length && (
          <p>结构化复盘规则：{result.calibrationRuleKeys.join("、")}</p>
        )}
        <p>
          方法：{result.methodVersion} · 价格口径：{result.priceBasis} · 标的：
          {result.subjectId}
        </p>
        {result.parameters && (
          <p>
            参数：
            {Object.entries(result.parameters)
              .map(
                ([key, value]) =>
                  `${key}=${Array.isArray(value) ? value.map((number) => numeric(number)).join(", ") : numeric(value)}`,
              )
              .join("；")}
          </p>
        )}
        {Object.entries(result.indicators).map(([name, value]) => (
          <p key={name}>
            {indicatorLabels[name] ?? name}：
            {numeric(
              value,
              name === "rsi14" ? 2 : result.subjectKind === "index" ? 2 : 4,
            )}
          </p>
        ))}
        {sourceHref(result.sourceUri) && (
          <a
            href={sourceHref(result.sourceUri)}
            target="_blank"
            rel="noreferrer"
          >
            原始价格来源
          </a>
        )}
      </details>
    </section>
  );
}

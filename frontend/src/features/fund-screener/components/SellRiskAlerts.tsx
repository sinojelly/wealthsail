import type { SellRiskAlerts as AlertData } from "../../../api/investment";
import { fundDisplayName, planPrice } from "../plan-display";
import { sourceHref, timeLabel } from "../macro-display";
const stateLabels = {
  STOP_LOSS: "触及止损参考",
  TAKE_PROFIT_T2: "达到止盈目标二",
  TAKE_PROFIT_T1: "达到止盈目标一",
  RSI_OVERBOUGHT: "RSI严重超买",
};
export default function SellRiskAlerts({ data }: { data?: AlertData }) {
  if (!data?.items.length) return null;
  return (
    <section
      className="op-personal-sell-alerts"
      role="alert"
      aria-label="持仓/跟踪卖出与风控提醒"
    >
      <h3>🔔 持仓/跟踪卖出与风控提醒</h3>
      {data.items.map((item) => (
        <article
          key={`${item.fundCode}:${item.trackId ?? item.reportRunId ?? item.planOrigin}:${item.state}`}
        >
          <p>
            <strong>
              {fundDisplayName(item.label, item.fundCode)} {item.fundCode}
            </strong>
            <span>
              {item.relationship
                .map((kind) => (kind === "HELD" ? "已持仓" : "已跟踪"))
                .join(" · ")}
            </span>
            <strong>{stateLabels[item.state] ?? "请核对风险"}</strong>
          </p>
          <p>
            {item.basis === "ESTIMATE_1430" ? "14:30估算净值" : "最新确认净值"}
            ：
            {planPrice(item.value, {
              subjectKind: "nav",
              priceBasis: "UNIT_NAV",
            })}{" "}
            元/份 · {timeLabel(item.asOf)}
            {item.rsi14 != null && Number.isFinite(Number(item.rsi14)) && (
              <>
                {" "}
                · RSI14 {Number(item.rsi14).toFixed(2)}（
                {timeLabel(item.rsiAsOf)}）
              </>
            )}
          </p>
          <small>
            {item.planOrigin === "FROZEN_STUDY"
              ? "按原跟踪冻结价格核对"
              : item.planOrigin === "DAILY_REPORT"
                ? "按有效日报计划核对"
                : "按真实净值技术指标核对"}
            {item.basis === "ESTIMATE_1430"
              ? "；估算值不是最终成交净值。"
              : "。"}
            {sourceHref(item.sourceUri) && (
              <a
                href={sourceHref(item.sourceUri)}
                target="_blank"
                rel="noreferrer"
              >
                价格来源
              </a>
            )}
          </small>
        </article>
      ))}
    </section>
  );
}

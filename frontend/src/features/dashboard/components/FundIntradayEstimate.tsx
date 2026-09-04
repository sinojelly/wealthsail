import Decimal from "decimal.js";
import type { FundIntradayEstimateDto } from "../../../api/types";

function signedPct(value: string): string {
  const decimal = new Decimal(value);
  return `${decimal.isPositive() ? "+" : ""}${decimal.toFixed(2)}%`;
}

function shanghaiTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

export default function FundIntradayEstimate({
  estimate,
}: {
  estimate: FundIntradayEstimateDto | null;
}) {
  if (!estimate) {
    return null;
  }
  const available = estimate.quality === "ESTIMATED" && estimate.value !== null;
  if (!available) {
    return (
      <div className="fund-intraday-estimate unavailable">
        <strong>盘中估值暂不可用</strong>
        <span>{estimate.explanation ?? "已披露成分覆盖不足，系统未发布估算值。"}</span>
        <small>官方净值不受影响，无需用户补录。</small>
      </div>
    );
  }

  return (
    <div
      className="fund-intraday-estimate"
      title="盘中估值由最新官方净值和已披露持仓成分估算，仅供跟踪"
    >
      <strong>盘中估值 {estimate.value}</strong>
      {estimate.changePct ? (
        <span className="estimate-change">
          估算涨跌 {signedPct(estimate.changePct)}
        </span>
      ) : null}
      {estimate.lower && estimate.upper ? (
        <span>区间 {estimate.lower}–{estimate.upper}</span>
      ) : null}
      {estimate.coveragePct ? (
        <span>成分覆盖 {estimate.coveragePct}%</span>
      ) : null}
      {estimate.disclosedOn ? <span>披露 {estimate.disclosedOn}</span> : null}
      <span>估值时间 {shanghaiTime(estimate.asOf)}</span>
      <small>仅供盘中跟踪，不是官方净值</small>
    </div>
  );
}

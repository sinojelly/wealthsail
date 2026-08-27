import { useId, useState } from "react";
import { ChevronDown, Info } from "lucide-react";
import type {
  DecimalString,
  FundRangeMetricsDto,
  HoldingPeriodDto,
} from "../../../api/types";

interface FundRangeMetricsProps {
  holdingPeriod: HoldingPeriodDto | null;
  metrics: FundRangeMetricsDto | null;
  currentNav: DecimalString | null;
  costReturnPct?: DecimalString | null;
}

function formatPct(value: DecimalString | null, signed = false) {
  if (value === null) return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  const rounded = numeric.toLocaleString("zh-CN", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 0,
  });
  return `${signed && numeric > 0 ? "+" : ""}${rounded}%`;
}

function holdingLabel(period: HoldingPeriodDto | null) {
  if (period?.days === null || period?.days === undefined) {
    return "持有起点待确认";
  }
  return `持有 ${period.days} 天`;
}

function statusLabel(status: FundRangeMetricsDto["status"]) {
  if (status === "PARTIAL") return "数据不完整";
  if (status === "CONFLICT") return "数据口径冲突";
  return null;
}

function methodLabel(method: HoldingPeriodDto["method"] | undefined) {
  const labels: Record<HoldingPeriodDto["method"], string> = {
    TRANSACTION_RECONCILED: "流水对账推导",
    PARTIAL_HISTORY: "部分历史推导",
    MISMATCH: "来源不一致",
    INSUFFICIENT_DATA: "数据不足",
    USER_CONFIRMED: "用户确认",
  };
  return method ? labels[method] : "方法待确认";
}

export default function FundRangeMetrics({
  holdingPeriod,
  metrics,
  currentNav,
  costReturnPct = null,
}: FundRangeMetricsProps) {
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();

  if (!metrics || metrics.status === "PENDING") {
    return (
      <div className="fund-range-compact" aria-live="polite">
        <span>{holdingLabel(holdingPeriod)}</span>
        <span className="metric-status pending">区间指标计算中</span>
      </div>
    );
  }

  const qualityStatus = statusLabel(metrics.status);
  const position = formatPct(metrics.rangePositionPct);
  const accessibleName = `${holdingLabel(holdingPeriod)}，区间位置 ${position}`;

  return (
    <div className="fund-range-metrics">
      <button
        aria-controls={detailId}
        aria-expanded={expanded}
        aria-label={accessibleName}
        className="fund-range-toggle"
        type="button"
        onClick={() => setExpanded((value) => !value)}
      >
        <span>{holdingLabel(holdingPeriod)}</span>
        <strong>区间位置 {position}</strong>
        <ChevronDown className={expanded ? "expanded" : ""} size={13} />
      </button>
      {qualityStatus ? (
        <span className={`metric-status ${metrics.status.toLowerCase()}`}>
          {qualityStatus}
        </span>
      ) : null}
      {expanded ? (
        <div className="fund-range-detail" id={detailId}>
          <dl>
            <div>
              <dt>本轮持有开始</dt>
              <dd>
                <strong>{holdingPeriod?.startedOn ?? "—"}</strong>
                <small>{methodLabel(holdingPeriod?.method)}</small>
              </dd>
            </div>
            <div>
              <dt>当前单位净值</dt>
              <dd>
                <strong>{currentNav ?? "—"}</strong>
                <small>{metrics.asOf ?? "披露日期待补齐"}</small>
              </dd>
            </div>
            <div>
              <dt>持有期最高</dt>
              <dd>
                <strong>{metrics.high ?? "—"}</strong>
                <small>{metrics.highDate ?? "日期待补齐"}</small>
              </dd>
            </div>
            <div>
              <dt>持有期最低</dt>
              <dd>
                <strong>{metrics.low ?? "—"}</strong>
                <small>{metrics.lowDate ?? "日期待补齐"}</small>
              </dd>
            </div>
            <div>
              <dt>当前位置</dt>
              <dd>
                <strong>
                  距区间最高 {formatPct(metrics.fromHighPct, true)}
                </strong>
                <small>较区间最低 {formatPct(metrics.fromLowPct, true)}</small>
              </dd>
            </div>
            {costReturnPct !== null ? (
              <div>
                <dt>持仓成本收益率</dt>
                <dd>{formatPct(costReturnPct, true)}</dd>
              </div>
            ) : null}
          </dl>
          {metrics.qualityReasons.length > 0 ? (
            <ul className="metric-quality-reasons">
              {metrics.qualityReasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
          <p className="metric-caveat">
            <Info size={12} aria-hidden="true" />
            当前值为最近公布的官方单位净值，不是盘中估值；不含累计分红再投资影响。
            区间统计截至 {metrics.asOf ?? "待补齐"}，不等同于投资收益率。
          </p>
        </div>
      ) : null}
    </div>
  );
}

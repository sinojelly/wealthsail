import { type FormEvent, useId, useState } from "react";
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
  onConfirmHoldingStart?: (startedOn: string) => Promise<void>;
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

function statusLabel(
  status: FundRangeMetricsDto["status"],
  qualityReasons: string[],
) {
  if (
    status === "PARTIAL" &&
    qualityReasons.includes("PARTIAL_HOLDING_HISTORY")
  ) {
    return "持有起点为估算";
  }
  if (status === "PARTIAL") return "区间数据不完整";
  if (status === "CONFLICT") return "数据口径冲突";
  return null;
}

function qualityReasonLabel(reason: string) {
  const labels: Record<string, string> = {
    PARTIAL_HOLDING_HISTORY:
      "当前区间按系统推导的持有开始日计算；确认或修改日期后，系统会自动重新抓取净值并重算。",
    NAV_DATE_GAP: "部分交易日缺少官方净值，区间结果可能不完整。",
    HOLDING_START_NOT_AVAILABLE: "尚未取得可用的持有开始日期。",
    METRICS_NOT_READY: "区间指标仍在后台生成。",
  };
  return labels[reason] ?? reason;
}

function methodLabel(method: HoldingPeriodDto["method"] | undefined) {
  const labels: Record<HoldingPeriodDto["method"], string> = {
    TRANSACTION_RECONCILED: "流水对账推导",
    PORTFOLIO_ACTIVITY_ESTIMATE: "组合/调仓估算",
    PARTIAL_HISTORY: "部分历史推导",
    MISMATCH: "来源不一致",
    INSUFFICIENT_DATA: "数据不足",
    USER_CONFIRMED: "用户确认",
  };
  return method ? labels[method] : "方法待确认";
}

function confidenceLabel(
  confidence: HoldingPeriodDto["confidence"] | undefined,
) {
  const labels: Record<HoldingPeriodDto["confidence"], string> = {
    HIGH: "高可信",
    MEDIUM: "中可信",
    LOW: "低可信",
    NONE: "无可信结论",
  };
  return confidence ? labels[confidence] : "可信度待确认";
}

export default function FundRangeMetrics({
  holdingPeriod,
  metrics,
  currentNav,
  costReturnPct = null,
  onConfirmHoldingStart,
}: FundRangeMetricsProps) {
  const [expanded, setExpanded] = useState(false);
  const [startedOn, setStartedOn] = useState(holdingPeriod?.startedOn ?? "");
  const [confirming, setConfirming] = useState(false);
  const [confirmationMessage, setConfirmationMessage] = useState<string | null>(
    null,
  );
  const [confirmationError, setConfirmationError] = useState<string | null>(null);
  const detailId = useId();

  async function submitHoldingStart(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!startedOn || !onConfirmHoldingStart) return;
    setConfirming(true);
    setConfirmationError(null);
    setConfirmationMessage(null);
    try {
      await onConfirmHoldingStart(startedOn);
      setConfirmationMessage("已确认，后台正在自动补齐净值并重新计算。");
    } catch (error) {
      setConfirmationError(
        error instanceof Error ? error.message : "确认失败，请稍后重试。",
      );
    } finally {
      setConfirming(false);
    }
  }

  if (!metrics || metrics.status === "PENDING") {
    return (
      <div className="fund-range-compact" aria-live="polite">
        <span>{holdingLabel(holdingPeriod)}</span>
        <span className="metric-status pending">区间指标计算中</span>
      </div>
    );
  }

  const qualityStatus = statusLabel(metrics.status, metrics.qualityReasons);
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
                <small>{confidenceLabel(holdingPeriod?.confidence)}</small>
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
                <li key={reason}>{qualityReasonLabel(reason)}</li>
              ))}
            </ul>
          ) : null}
          {holdingPeriod?.canConfirm && onConfirmHoldingStart ? (
            <form
              className="holding-start-confirmation"
              onSubmit={submitHoldingStart}
            >
              <div>
                <label htmlFor={`${detailId}-holding-start`}>
                  确认持有开始日期
                </label>
                <small>日期准确可直接确认；有误时修改后再确认。</small>
              </div>
              <input
                id={`${detailId}-holding-start`}
                type="date"
                value={startedOn}
                onChange={(event) => setStartedOn(event.target.value)}
              />
              <button
                className="btn-secondary"
                type="submit"
                disabled={!startedOn || confirming}
              >
                {confirming ? "正在提交…" : "确认并后台重算"}
              </button>
              {confirmationMessage ? (
                <p className="holding-start-success" role="status">
                  {confirmationMessage}
                </p>
              ) : null}
              {confirmationError ? (
                <p className="form-error" role="alert">
                  {confirmationError}
                </p>
              ) : null}
            </form>
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

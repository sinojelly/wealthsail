import { useState, type ReactNode } from "react";
import {
  CircleMinus,
  CirclePlus,
  Equal,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import type {
  TiantianComparisonStatus,
  TiantianImportDetail,
} from "../../../api/types";

const STATUS_LABEL: Record<TiantianComparisonStatus, string> = {
  NEW: "新增",
  UNCHANGED: "无变化",
  CHANGED: "有变化",
  CLOSED: "将清仓",
  CONFLICT: "冲突",
};

const STATUS_ICON: Record<TiantianComparisonStatus, ReactNode> = {
  NEW: <CirclePlus size={12} aria-hidden="true" />,
  UNCHANGED: <Equal size={12} aria-hidden="true" />,
  CHANGED: <RefreshCw size={12} aria-hidden="true" />,
  CLOSED: <CircleMinus size={12} aria-hidden="true" />,
  CONFLICT: <TriangleAlert size={12} aria-hidden="true" />,
};

type Tab = "positions" | "history" | "issues";

interface Props {
  detail: TiantianImportDetail;
  decisions: Record<string, string>;
  onDecision: (itemId: string, value: string) => void;
}

export default function ImportPreviewStep({
  detail,
  decisions,
  onDecision,
}: Props) {
  const [tab, setTab] = useState<Tab>("positions");
  const tabs: Array<{ id: Tab; label: string }> = [
    { id: "positions", label: "当前持仓" },
    { id: "history", label: "历史流水" },
    { id: "issues", label: "问题与提示" },
  ];
  return (
    <div className="tiantian-preview">
      <div className="import-summary-grid" aria-label="持仓差异汇总">
        <span>持仓 {detail.summary.total}</span>
        <span>新增 {detail.summary.new}</span>
        <span>无变化 {detail.summary.unchanged}</span>
        <span>变化 {detail.summary.changed}</span>
        <span>清仓 {detail.summary.closed}</span>
        <span className={detail.summary.conflict ? "negative" : ""}>
          冲突 {detail.summary.conflict}
        </span>
      </div>
      <div className="import-tab-list" role="tablist" aria-label="导入预览内容">
        {tabs.map((item) => (
          <button
            key={item.id}
            id={`tiantian-tab-${item.id}`}
            role="tab"
            type="button"
            aria-selected={tab === item.id}
            aria-controls={`tiantian-panel-${item.id}`}
            tabIndex={tab === item.id ? 0 : -1}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      {tab === "positions" ? (
        <div
          id="tiantian-panel-positions"
          role="tabpanel"
          aria-labelledby="tiantian-tab-positions"
          className="import-position-list"
        >
          {detail.positions.map((position) => (
            <article className="import-position-row" key={position.itemId}>
              <div>
                <strong>{position.name}</strong>
                <small className="mono">{position.symbol}</small>
              </div>
              <span
                className={`import-status status-${position.comparisonStatus.toLowerCase()}`}
              >
                {STATUS_ICON[position.comparisonStatus]}
                {STATUS_LABEL[position.comparisonStatus]}
              </span>
              <div className="import-position-values">
                <span>份额 {position.quantity}</span>
                <span>成本 {position.unitCost}</span>
              </div>
              <label className="field-stack compact-field">
                <span>{position.name}持有开始日期</span>
                <input
                  aria-label={`${position.name}持有开始日期`}
                  type="date"
                  max={detail.package.positionsAsOf?.slice(0, 10) ?? undefined}
                  value={decisions[position.itemId] ?? ""}
                  onChange={(event) =>
                    onDecision(position.itemId, event.target.value)
                  }
                />
                <small>
                  {position.candidateHoldingStartedOn
                    ? `系统推导：${position.candidateHoldingStartedOn}（${position.holdingStartStatus}）`
                    : `系统未能可靠推导（${position.holdingStartStatus}），可人工确认。`}
                </small>
              </label>
            </article>
          ))}
        </div>
      ) : null}
      {tab === "history" ? (
        <div
          id="tiantian-panel-history"
          role="tabpanel"
          aria-labelledby="tiantian-tab-history"
          className="import-tab-panel"
        >
          <p>可导入 {detail.historySummary.importable} 条</p>
          <p>重复 {detail.historySummary.duplicated} 条</p>
          <p>需复核 {detail.historySummary.reviewRequired ?? 0} 条</p>
          <p>排除活期宝 {detail.excludedSummary.cashManagement} 条</p>
          <p>排除非入账记录 {detail.excludedSummary.nonPosting} 条</p>
          <p>
            数据范围 {detail.package.sourceWindowStart ?? "未知"} 至{" "}
            {detail.package.sourceWindowEnd ?? "未知"}
          </p>
          <small>
            历史流水保存为来源事实，不会自动伪造成买入、卖出或现金流水。
          </small>
        </div>
      ) : null}
      {tab === "issues" ? (
        <div
          id="tiantian-panel-issues"
          role="tabpanel"
          aria-labelledby="tiantian-tab-issues"
          className="import-tab-panel"
        >
          {detail.blockingErrors.length === 0 &&
          detail.warnings.length === 0 ? (
            <p className="form-ok">未发现阻断问题。</p>
          ) : null}
          {detail.blockingErrors.map((message) => (
            <p className="form-error" key={`blocking-${message}`}>
              {message}
            </p>
          ))}
          {detail.warnings.map((message) => (
            <p className="import-warning" key={`warning-${message}`}>
              {message}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

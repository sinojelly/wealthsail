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

const BLOCKING_MESSAGE: Record<string, string> = {
  UNKNOWN_FUND_CODE: "部分基金代码未通过权威主数据核验，请稍后重试",
  ACCOUNT_LEDGER_CONFLICT:
    "目标账户中存在其他来源的持仓事实，需要先处理账户冲突",
  SOURCE_BINDING_ACCOUNT_CONFLICT: "该天天基金来源账号已绑定到另一个投资账户",
  SOURCE_SNAPSHOT_OLDER_THAN_CONFIRMED:
    "上传快照早于已经确认的快照，不能覆盖较新数据",
  SOURCE_TRANSACTION_HASH_CONFLICT:
    "同一来源流水编号对应了不同内容，需要人工核查",
  HOLDING_SHARE_MISMATCH: "历史流水推导的份额与当前快照不一致，需要人工核查",
  EMPTY_SNAPSHOT_WITHOUT_BASELINE: "空持仓快照不能用于创建新的账户基线",
};

const WARNING_MESSAGE: Record<string, string> = {
  PARTIAL_HISTORY: "历史流水覆盖不完整，持有开始日期可能只能部分推导",
  HOLDING_START_INSUFFICIENT: "部分基金缺少足够流水，持有开始日期可人工补充",
  REVIEW_REQUIRED_RECORDS:
    "存在无法安全分类的历史记录，已保留待复核且不会生成买卖或资金流水",
};

const POSITION_ERROR_MESSAGE: Record<string, string> = {
  UNKNOWN_FUND_CODE: "基金代码未通过权威主数据核验，暂不能导入",
  ACCOUNT_LEDGER_CONFLICT: "目标账户中存在其他来源的持仓事实",
};

function messageFor(code: string, messages: Record<string, string>): string {
  return messages[code] ?? code;
}

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
                {position.validationErrors?.map((code) => (
                  <small
                    className="form-error import-position-error"
                    key={`${position.itemId}-${code}`}
                  >
                    {messageFor(code, POSITION_ERROR_MESSAGE)}
                  </small>
                ))}
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
              {messageFor(message, BLOCKING_MESSAGE)}
            </p>
          ))}
          {detail.warnings.map((message) => (
            <p className="import-warning" key={`warning-${message}`}>
              {messageFor(message, WARNING_MESSAGE)}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

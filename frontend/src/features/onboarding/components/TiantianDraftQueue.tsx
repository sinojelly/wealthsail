import { AlertTriangle, Clock3, RotateCcw } from "lucide-react";
import type { TiantianImportListItem } from "../../../api/types";
import { freshnessReasonText } from "./import-governance-copy";

interface Props {
  items: TiantianImportListItem[];
  loading: boolean;
  error: string | null;
  canWrite: boolean;
  cancellingBatchId: string | null;
  onOpen: (item: TiantianImportListItem) => void;
  onCancel: (item: TiantianImportListItem) => void;
  onReparse: (item: TiantianImportListItem) => void;
}

export default function TiantianDraftQueue({
  items,
  loading,
  error,
  canWrite,
  cancellingBatchId,
  onOpen,
  onCancel,
  onReparse,
}: Props) {
  return (
    <section
      className="panel tiantian-draft-queue"
      aria-labelledby="draft-queue-title"
    >
      <div className="panel-header">
        <div>
          <h2 id="draft-queue-title">
            <Clock3 size={17} aria-hidden="true" />
            待处理草稿
          </h2>
          <p>无需重新上传即可继续核对、确认或取消；重新解析仍需选择原 ZIP。</p>
        </div>
        {items.length > 0 ? (
          <span className="draft-count">{items.length} 个待处理</span>
        ) : null}
      </div>
      <div className="panel-body">
        {loading ? <p className="muted">正在读取待处理草稿…</p> : null}
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        {!loading && !error && items.length === 0 ? (
          <p className="compact-empty-state">
            当前没有待处理草稿，可上传 ZIP 创建新预览。
          </p>
        ) : null}
        <div className="tiantian-draft-list">
          {items.map((item) => {
            const actions = item.availableActions ?? [
              "VIEW",
              "CANCEL",
              "REPARSE",
            ];
            const isFresh = item.freshness === "FRESH";
            const isValid = item.validationStatus === "VALID";
            let statusCopy = "已过期，需要重新解析";
            if (!isValid) statusCopy = "存在问题，暂不能确认";
            else if (isFresh) statusCopy = "有效，可继续确认";
            return (
              <article className="tiantian-draft-card" key={item.batchId}>
                <div className="draft-card-main">
                  <div>
                    <strong>{item.account.name}</strong>
                    <span>
                      快照 {item.sourceAsOf.slice(0, 10)} · 更新{" "}
                      {item.updatedAt?.slice(0, 10) ??
                        item.createdAt.slice(0, 10)}
                    </span>
                  </div>
                  <span
                    className={`draft-health ${isFresh && isValid ? "is-fresh" : "is-stale"}`}
                  >
                    {!isFresh || !isValid ? (
                      <AlertTriangle size={13} aria-hidden="true" />
                    ) : null}
                    {statusCopy}
                  </span>
                </div>
                <div className="draft-summary" aria-label="草稿持仓变化摘要">
                  <span>新增 {item.summary.new}</span>
                  <span>变化 {item.summary.changed}</span>
                  <span>不变 {item.summary.unchanged}</span>
                  <span>清仓 {item.summary.closed}</span>
                  <span>冲突 {item.summary.conflict}</span>
                </div>
                {(item.freshnessReasons ?? []).length > 0 ? (
                  <ul className="draft-reasons">
                    {item.freshnessReasons.map((reason) => (
                      <li key={reason}>{freshnessReasonText(reason)}</li>
                    ))}
                  </ul>
                ) : null}
                <div className="draft-card-actions">
                  <button
                    className="btn-secondary"
                    type="button"
                    aria-label={`继续核对 ${item.account.name} DRAFT`}
                    onClick={() => onOpen(item)}
                  >
                    继续核对
                  </button>
                  {canWrite && actions.includes("REPARSE") ? (
                    <button
                      className="btn-secondary"
                      type="button"
                      onClick={() => onReparse(item)}
                    >
                      <RotateCcw size={13} aria-hidden="true" />
                      重新解析
                    </button>
                  ) : null}
                  {canWrite && actions.includes("CANCEL") ? (
                    <button
                      className="btn-quiet-danger"
                      type="button"
                      disabled={cancellingBatchId === item.batchId}
                      onClick={() => onCancel(item)}
                    >
                      {cancellingBatchId === item.batchId
                        ? "取消中…"
                        : "取消草稿"}
                    </button>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}

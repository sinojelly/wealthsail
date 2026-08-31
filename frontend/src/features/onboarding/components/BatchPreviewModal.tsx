import { AlertCircle, CheckCircle2, Database, ShieldCheck } from "lucide-react";
import Dialog from "../../../components/accessibility/Dialog";
import type { ImportRow } from "../import-parser";

export default function BatchPreviewModal({
  open,
  rows,
  onClose,
  onSubmit,
  submitting,
  error,
  canSubmit,
}: {
  open: boolean;
  rows: ImportRow[];
  onClose: () => void;
  onSubmit: () => void;
  submitting: boolean;
  error: string | null;
  canSubmit: boolean;
}) {
  const validCount = rows.filter((row) => row.valid).length;
  const invalidCount = rows.length - validCount;
  return (
    <Dialog open={open} title="批次导入预览" size="xl" onClose={onClose}>
      <div className="dialog-content batch-preview">
        <div className="batch-stats">
          <div className="valid-stat">
            <CheckCircle2 size={18} />
            <p>
              <strong>{validCount} 条可导入</strong>
              <span>字段与十进制格式通过校验</span>
            </p>
          </div>
          <div className="invalid-stat">
            <AlertCircle size={18} />
            <p>
              <strong>{invalidCount} 条需修正</strong>
              <span>整批提交前必须修正</span>
            </p>
          </div>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>行</th>
                <th>资产类型</th>
                <th>代码</th>
                <th>名称</th>
                <th className="numeric">份额</th>
                <th className="numeric">成本/参考价</th>
                <th>校验结果</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  className={row.valid ? "" : "invalid-row"}
                  key={row.rowNumber}
                >
                  <td className="mono">{row.rowNumber}</td>
                  <td>
                    <span className="tag">{row.assetType || "—"}</span>
                  </td>
                  <td className="mono">{row.symbol || "—"}</td>
                  <td>{row.name || "—"}</td>
                  <td className="mono numeric">{row.quantity || "—"}</td>
                  <td className="mono numeric">{row.referencePrice || "—"}</td>
                  <td>
                    {row.valid ? (
                      <span className="row-valid">
                        <CheckCircle2 size={11} />
                        通过
                      </span>
                    ) : (
                      <span className="row-invalid">
                        <AlertCircle size={11} />
                        {row.issue}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="batch-boundary">
          <ShieldCheck size={15} />
          <p>
            <strong>提交边界</strong>
            <span>
              服务端会重新核验全部代码；任一行失败则整批不入账。
              成功导入使用单租户事务、幂等批次并保留审计记录。
            </span>
          </p>
        </div>
        {error ? (
          <p className="field-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      <footer className="dialog-footer">
        <span className="commit-note">
          <Database size={12} />
          将原子提交 {validCount} 条有效记录
        </span>
        <button
          className="btn-secondary"
          disabled={submitting}
          onClick={onClose}
        >
          返回修正
        </button>
        <button
          className="btn-primary"
          disabled={
            submitting || !canSubmit || rows.length === 0 || invalidCount > 0
          }
          title={!canSubmit ? "VIEWER 角色仅可查看持仓" : undefined}
          onClick={onSubmit}
        >
          {submitting ? "提交中…" : "批次提交"}
        </button>
      </footer>
    </Dialog>
  );
}

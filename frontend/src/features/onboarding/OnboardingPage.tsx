import { useState } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  Clock3,
  Database,
  FileCheck2,
  ShieldCheck,
} from "lucide-react";
import { Link } from "react-router-dom";
import PageHeader from "../../components/layout/PageHeader";
import BatchPreviewModal from "./components/BatchPreviewModal";
import ImportBatchForm from "./components/ImportBatchForm";
import { parseImportRows, type ImportRow } from "./import-parser";

export default function OnboardingPage() {
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [importedCount, setImportedCount] = useState<number | null>(null);
  function preview(source: string) {
    setRows(parseImportRows(source));
    setPreviewOpen(true);
    setImportedCount(null);
  }
  return (
    <>
      <PageHeader
        title="初始化导入"
        description="把股票、场内基金和场外基金持仓安全地导入租户事实账本"
        actions={
          <Link className="btn-secondary" to="/dashboard">
            <ArrowLeft size={14} />
            返回资产看板
          </Link>
        }
      />
      {importedCount !== null ? (
        <div className="alert import-success">
          <CheckCircle2 size={16} />
          <div>
            <strong>已导入 {importedCount} 条有效记录</strong>
            <p>
              导入批次、原始输入哈希和逐行结果已写入审计日志；无效行未写入。
            </p>
          </div>
        </div>
      ) : null}
      <div className="onboarding-layout">
        <ImportBatchForm onPreview={preview} />
        <aside className="onboarding-aside">
          <section className="panel">
            <div className="panel-header">
              <h2>导入流程</h2>
            </div>
            <ol className="import-steps">
              <li>
                <span>
                  <FileCheck2 size={15} />
                </span>
                <p>
                  <strong>字段校验</strong>
                  <small>资产标识、精度和必填项</small>
                </p>
              </li>
              <li>
                <span>
                  <ShieldCheck size={15} />
                </span>
                <p>
                  <strong>冲突预览</strong>
                  <small>不覆盖既有事实，明确跳过原因</small>
                </p>
              </li>
              <li>
                <span>
                  <Database size={15} />
                </span>
                <p>
                  <strong>批次提交</strong>
                  <small>单租户事务与逐行结果</small>
                </p>
              </li>
              <li>
                <span>
                  <Clock3 size={15} />
                </span>
                <p>
                  <strong>审计留痕</strong>
                  <small>来源、actor、request_id 与哈希</small>
                </p>
              </li>
            </ol>
          </section>
          <section className="panel import-history">
            <div className="panel-header">
              <h2>最近导入</h2>
            </div>
            <div className="panel-body">
              <div>
                <strong className="mono">BATCH-20260715-01</strong>
                <span>2026-07-15 08:42</span>
                <small>6 条成功 · 0 条失败</small>
              </div>
              <div>
                <strong className="mono">BATCH-20260702-02</strong>
                <span>2026-07-02 20:18</span>
                <small>2 条成功 · 1 条跳过</small>
              </div>
            </div>
          </section>
        </aside>
      </div>
      <BatchPreviewModal
        open={previewOpen}
        rows={rows}
        onClose={() => setPreviewOpen(false)}
        onCommit={(count) => {
          setImportedCount(count);
          setPreviewOpen(false);
        }}
      />
    </>
  );
}

import { useState } from "react";
import {
  ArrowLeft,
  Clock3,
  Database,
  FileCheck2,
  ShieldCheck,
} from "lucide-react";
import { Link } from "react-router-dom";
import PageHeader from "../../components/layout/PageHeader";
import BatchPreviewModal from "./components/BatchPreviewModal";
import ImportBatchForm from "./components/ImportBatchForm";
import TiantianImportWizard from "./components/TiantianImportWizard";
import { parseImportRows, type ImportRow } from "./import-parser";

export default function OnboardingPage() {
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [previewOpen, setPreviewOpen] = useState(false);
  function preview(source: string) {
    setRows(parseImportRows(source));
    setPreviewOpen(true);
  }
  return (
    <>
      <PageHeader
        title="持仓导入与更新"
        description="导入股票、场内基金和场外基金；所有外部快照先预览、确认，再进入租户事实账本"
        actions={
          <Link className="btn-secondary" to="/dashboard">
            <ArrowLeft size={14} />
            返回资产看板
          </Link>
        }
      />
      <TiantianImportWizard />
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
              <p className="muted">
                后端导入命令与批次历史尚未接入；本页当前只做本地格式校验和预览，
                不会写入账本。
              </p>
            </div>
          </section>
        </aside>
      </div>
      <BatchPreviewModal
        open={previewOpen}
        rows={rows}
        onClose={() => setPreviewOpen(false)}
      />
    </>
  );
}

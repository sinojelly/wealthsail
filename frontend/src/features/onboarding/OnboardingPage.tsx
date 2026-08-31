import { useRef, useState } from "react";
import {
  ArrowLeft,
  Clock3,
  Database,
  FileCheck2,
  ShieldCheck,
} from "lucide-react";
import { Link } from "react-router-dom";
import { HttpError } from "../../api/client";
import { useCsvPositionImport } from "../../api/hooks";
import type { CsvPositionImportResult } from "../../api/types";
import { useAuthSession } from "../../app/auth-session";
import EmptyState from "../../components/data-state/EmptyState";
import PageHeader from "../../components/layout/PageHeader";
import BatchPreviewModal from "./components/BatchPreviewModal";
import ImportBatchForm from "./components/ImportBatchForm";
import TiantianImportWizard from "./components/TiantianImportWizard";
import { parseImportRows, type ImportRow } from "./import-parser";
import { createIdempotencyKey } from "../../lib/idempotency";

export default function OnboardingPage() {
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [source, setSource] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [csvError, setCsvError] = useState<string | null>(null);
  const [csvResult, setCsvResult] = useState<CsvPositionImportResult | null>(
    null,
  );
  const commandRef = useRef<{ source: string; key: string } | null>(null);
  const session = useAuthSession();
  const canSubmit = Boolean(session && session.role !== "VIEWER");
  const csvImport = useCsvPositionImport();

  function preview(source: string) {
    setSource(source);
    setRows(parseImportRows(source));
    setCsvError(null);
    setPreviewOpen(true);
  }

  async function submitCsv() {
    if (!canSubmit || rows.length === 0 || rows.some((row) => !row.valid)) {
      return;
    }
    if (commandRef.current?.source !== source) {
      commandRef.current = {
        source,
        key: `csv-import-${createIdempotencyKey()}`,
      };
    }
    setCsvError(null);
    try {
      const result = await csvImport.mutateAsync({
        csvData: source,
        idempotencyKey: commandRef.current.key,
      });
      setCsvResult(result);
      setPreviewOpen(false);
    } catch (error) {
      const detail =
        error instanceof HttpError && error.fieldErrors.length > 0
          ? error.fieldErrors.map((item) => item.message).join("；")
          : error instanceof Error
            ? error.message
            : "CSV 导入失败，请稍后重试";
      setCsvError(detail);
    }
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
              {csvResult ? (
                <div className="import-history-list">
                  <div>
                    <strong>已导入 {csvResult.importedCount} 条有效记录</strong>
                    <span>批次 {csvResult.batchId}</span>
                    <small>
                      {csvResult.replayed ? "幂等重放" : "已确认并写入账本"}
                    </small>
                  </div>
                </div>
              ) : (
                <EmptyState
                  className="onboarding-empty-state"
                  title="暂无 CSV 批次记录"
                  detail="成功提交 CSV 后，最近一次确认结果会显示在这里。"
                  icon={Clock3}
                />
              )}
            </div>
          </section>
        </aside>
      </div>
      <BatchPreviewModal
        open={previewOpen}
        rows={rows}
        onClose={() => setPreviewOpen(false)}
        onSubmit={() => void submitCsv()}
        submitting={csvImport.isPending}
        error={csvError}
        canSubmit={canSubmit}
      />
    </>
  );
}

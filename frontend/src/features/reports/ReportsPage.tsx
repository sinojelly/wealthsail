import { useState } from "react";
import { RefreshCw, Sparkles } from "lucide-react";
import { useGenerateReport, useReportDetail, useReportsList } from "../../api/hooks";
import type { ReportSession } from "../../api/types";
import PageHeader from "../../components/layout/PageHeader";
import EvidenceDrawer from "./components/EvidenceDrawer";
import ReportTimeline from "./components/ReportTimeline";
import ReportViewer from "./components/ReportViewer";
import type { EvidenceRecord } from "./report-data";

export default function ReportsPage() {
  const reportsQuery = useReportsList();
  const reports = reportsQuery.data ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = reports.find((report) => report.id === selectedId) ?? null;
  const detailQuery = useReportDetail(selected?.id ?? null);
  const generate = useGenerateReport();
  const [evidence, setEvidence] = useState<EvidenceRecord | null>(null);

  function handleGenerate(session: ReportSession) {
    if (!selected) return;
    generate.mutate(session);
  }

  return (
    <>
      <PageHeader
        title="研报审计"
        description="四时段报告的生成、版本审计与证据回看"
        actions={
          <button
            className="btn-secondary"
            disabled={!selected || generate.isPending}
            onClick={() => selected && handleGenerate(selected.session)}
          >
            <RefreshCw size={14} className={generate.isPending ? "spin" : ""} />
            重新生成 {selected ? `v${selected.version + 1}` : ""}
          </button>
        }
      />
      {generate.isError ? (
        <div className="alert alert-danger">
          <Sparkles size={15} />
          生成失败：{generate.error instanceof Error ? generate.error.message : "未知错误"}
        </div>
      ) : null}
      <div className="reports-workspace">
        <ReportTimeline
          reports={reports}
          selectedId={selected?.id ?? ""}
          onSelect={(report) => setSelectedId(report.id)}
        />
        {detailQuery.isLoading ? (
          <div className="report-viewer report-loading">正在加载报告…</div>
        ) : detailQuery.error ? (
          <div className="report-viewer report-loading">
            报告加载失败：
            {detailQuery.error instanceof Error
              ? detailQuery.error.message
              : "未知错误"}
          </div>
        ) : detailQuery.data ? (
          <ReportViewer report={detailQuery.data} onEvidence={setEvidence} />
        ) : (
          <div className="report-viewer report-loading">
            {reports.length === 0 ? "暂无研报" : "请选择一份报告"}
          </div>
        )}
        <EvidenceDrawer evidence={evidence} onClose={() => setEvidence(null)} />
      </div>
    </>
  );
}

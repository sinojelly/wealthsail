import { useState } from "react";
import { FileSearch, RefreshCw, Sparkles } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import {
  useGenerateReport,
  useReportDetail,
  useReportsList,
} from "../../api/hooks";
import type { ReportSession, ReportSummary } from "../../api/types";
import PageHeader from "../../components/layout/PageHeader";
import EmptyState from "../../components/data-state/EmptyState";
import EvidenceDrawer from "./components/EvidenceDrawer";
import ReportTimeline from "./components/ReportTimeline";
import ReportViewer from "./components/ReportViewer";
import type { EvidenceRecord } from "./report-data";

type TriggerVariant = {
  phase?: "MACRO" | "PERSONALIZED";
  userId?: string;
};

// 根据选中的报告行推断应该触发的变体与目标用户：选中 MACRO 行 → 重建
// 宏观；选中 PERSONALIZED 行 → 重建该用户的个性化简报（投到该用户企微）。
// 不识别 STANDARD（33c6c7b 已下线旧生成入口），保留默认空对象以触发 MACRO
// 默认路径，避免按钮完全失灵。
function pickTriggerVariant(
  report: ReportSummary,
): TriggerVariant {
  if (report.variant === "PRE_MARKET_MACRO") {
    return { phase: "MACRO" };
  }
  if (report.variant === "PRE_MARKET_PERSONALIZED" && report.audienceUserId) {
    return { phase: "PERSONALIZED", userId: report.audienceUserId };
  }
  return {};
}

export default function ReportsPage() {
  const reportsQuery = useReportsList();
  const reports = reportsQuery.data ?? [];
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("reportId");
  const selected = reports.find((report) => report.id === selectedId) ?? null;
  const detailQuery = useReportDetail(selected?.id ?? null);
  const generate = useGenerateReport();
  const [evidence, setEvidence] = useState<EvidenceRecord | null>(null);

  function handleGenerate() {
    if (!selected) return;
    const variant = pickTriggerVariant(selected);
    generate.mutate({
      session: selected.session,
      phase: variant.phase,
      userId: variant.userId,
    });
  }

  // 无任何历史报告时的时段占位入口：直接按 session 触发（后端 tenant 级
  // 生成 + outbox 投递），让 14:30/盘后等时段也能一键推送而不依赖手工命令。
  function handleGenerateSession(session: ReportSession) {
    generate.mutate({ session });
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
            onClick={() => selected && handleGenerate()}
          >
            <RefreshCw size={14} className={generate.isPending ? "spin" : ""} />
            重新生成 {selected ? `v${selected.version + 1}` : ""}
          </button>
        }
      />
      {generate.isError ? (
        <div className="alert alert-danger">
          <Sparkles size={15} />
          生成失败：
          {generate.error instanceof Error
            ? generate.error.message
            : "未知错误"}
        </div>
      ) : null}
      <div className="reports-workspace">
        <ReportTimeline
          reports={reports}
          selectedId={selected?.id ?? ""}
          onSelect={(report) => {
            const next = new URLSearchParams(searchParams);
            next.set("reportId", report.id);
            setSearchParams(next);
          }}
          onGenerate={handleGenerateSession}
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
          <EmptyState
            className="report-viewer-empty"
            title={reports.length === 0 ? "暂无研报" : "请选择一份报告"}
            detail={
              reports.length === 0
                ? "盘前、盘中、基金 2:30 和盘后报告生成后，会在这里集中查看并保留版本审计记录。"
                : "从左侧时间线选择报告，即可查看摘要、证据链和历史版本。"
            }
            icon={FileSearch}
          />
        )}
        <EvidenceDrawer evidence={evidence} onClose={() => setEvidence(null)} />
      </div>
    </>
  );
}

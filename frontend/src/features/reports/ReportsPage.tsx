import { useState } from "react";
import { FileSearch, RefreshCw, Sparkles } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { canViewOperations, useAuthSession } from "../../app/auth-session";
import {
  useGenerateReport,
  useReportDetail,
  useReportsList,
} from "../../api/hooks";
import type { ReportSession, ReportSummary } from "../../api/types";
import type { TriggerReportResult } from "../../api/reports";
import PageHeader from "../../components/layout/PageHeader";
import EmptyState from "../../components/data-state/EmptyState";
import EvidenceDrawer from "./components/EvidenceDrawer";
import ReportTimeline from "./components/ReportTimeline";
import ReportViewer from "./components/ReportViewer";
import type { EvidenceRecord } from "./report-data";

type TriggerVariant = {
  phase?: "MACRO" | "PERSONALIZED" | "ALERT";
};

// 只从选中行恢复报告变体；受众与收件人始终由后端绑定到当前登录用户。
// STANDARD 无需 phase，盘前宏观、盘前个性化和盘中告警则必须保持原变体。
function pickTriggerVariant(report: ReportSummary): TriggerVariant {
  if (report.variant === "PRE_MARKET_MACRO") {
    return { phase: "MACRO" };
  }
  if (report.variant === "PRE_MARKET_PERSONALIZED" && report.audienceUserId) {
    return { phase: "PERSONALIZED" };
  }
  if (report.variant === "INTRADAY_ALERT") {
    return { phase: "ALERT" };
  }
  return {};
}

export default function ReportsPage() {
  const session = useAuthSession();
  const canTrigger = canViewOperations(session);
  const reportsQuery = useReportsList();
  const reports = reportsQuery.data ?? [];
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("reportId");
  const selected = reports.find((report) => report.id === selectedId) ?? null;
  const detailQuery = useReportDetail(selected?.id ?? null);
  const generate = useGenerateReport();
  const [evidence, setEvidence] = useState<EvidenceRecord | null>(null);
  const [generationNotice, setGenerationNotice] = useState<string | null>(null);

  function handleGenerationResult(result: TriggerReportResult) {
    if (!result.reportId) {
      setGenerationNotice("当前没有满足生成条件的内容，未创建新报告。");
      return;
    }

    const next = new URLSearchParams(searchParams);
    next.set("reportId", result.reportId);
    setSearchParams(next);
    setGenerationNotice(
      result.outboxId
        ? "报告已生成，企业微信通知已排队。"
        : "报告已生成；当前没有可用的企业微信投递。",
    );
  }

  function runGeneration(input: {
    session: ReportSession;
    phase?: "MACRO" | "PERSONALIZED" | "ALERT";
  }) {
    setGenerationNotice(null);
    generate.mutate(input, { onSuccess: handleGenerationResult });
  }

  function handleGenerate() {
    if (!selected || !canTrigger) return;
    const variant = pickTriggerVariant(selected);
    runGeneration({
      session: selected.session,
      phase: variant.phase,
    });
  }

  // 无历史报告时直接按 session 触发；报告可能是租户共享内容，但手动
  // Outbox 始终只面向当前操作人。
  function handleGenerateSession(session: ReportSession) {
    if (!canTrigger) return;
    runGeneration({ session });
  }

  return (
    <>
      <PageHeader
        title="研报审计"
        description="四时段报告的生成、版本审计与证据回看"
        actions={
          canTrigger ? (
            <button
              className="btn-secondary"
              disabled={!selected || generate.isPending}
              onClick={() => selected && handleGenerate()}
            >
              <RefreshCw
                size={14}
                className={generate.isPending ? "spin" : ""}
              />
              重新生成并发给我 {selected ? `v${selected.version + 1}` : ""}
            </button>
          ) : null
        }
      />
      {generationNotice ? (
        <div className="alert alert-info" role="status">
          <Sparkles size={15} />
          {generationNotice}
        </div>
      ) : null}
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
          canGenerate={canTrigger}
          isGenerating={generate.isPending}
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

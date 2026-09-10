import {
  AlertTriangle,
  BookOpen,
  ExternalLink,
  FlaskConical,
  History,
  Info,
  ListChecks,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import QualityBadge from "../../../components/data-state/QualityBadge";
import { reportDisplayMeta, type EvidenceRecord } from "../report-data";
import type { ReportDetail, ReportEvidence, ReportItem } from "../../../api/types";
import {
  formatShanghaiDateTime,
  formatShanghaiTime,
} from "../../../lib/shanghai-time";
import ReportMarkdown from "./ReportMarkdown";

const ITEM_TYPE_META: Record<string, { label: string; icon: typeof Info }> = {
  FACT: { label: "事实", icon: BookOpen },
  RISK: { label: "风险", icon: ShieldAlert },
  ACTION_PLAN: { label: "行动计划", icon: ListChecks },
  TODO: { label: "待办", icon: ListChecks },
  FUND_ESTIMATE: { label: "基金估值", icon: FlaskConical },
  ALERT: { label: "告警", icon: AlertTriangle },
};

function itemToEvidence(item: ReportItem, evidence: ReportEvidence): EvidenceRecord {
  return {
    id: evidence.id,
    type: ITEM_TYPE_META[item.itemType]?.label ?? item.itemType,
    title: item.title,
    content: evidence.excerpt || item.body,
    source: evidence.sourceName || "来源未命名",
    sourceUrl:
      evidence.originUri && !evidence.originUri.startsWith("internal:")
        ? evidence.originUri
        : undefined,
    asOf: evidence.asOf,
    claimType: evidence.claimType,
    sourceSnapshotId: evidence.sourceSnapshotId || undefined,
    quality: evidence.quality,
    hash: evidence.contentHash || undefined,
  };
}

function evidenceLabel(report: ReportDetail, evidence: ReportEvidence): string {
  const sourceName = evidence.sourceName?.trim();
  if (sourceName) return sourceName;
  // A valuation snapshot is still a real persisted source even when a legacy
  // row predates a named data-source record; avoid displaying an opaque ID.
  return report.session === "FUND_1430" ? "估值系统" : "来源快照";
}

export default function ReportViewer({
  report,
  onEvidence,
}: {
  report: ReportDetail;
  onEvidence: (evidence: EvidenceRecord) => void;
}) {
  const asOfLabel = formatShanghaiDateTime(report.asOf);
  const display = reportDisplayMeta(report);
  const unlinkedItemCount = report.items.filter(
    (item) => item.evidence.length === 0,
  ).length;
  return (
    <article className="report-viewer">
      <header className="report-viewer-header">
        <div>
          <span className="report-kicker">
            {report.tradeDate} · {asOfLabel} 生成
          </span>
          <h1>{display.title}</h1>
          <p>{display.subtitle}</p>
        </div>
        <div className="report-version">
          <span className="status-pill published">
            {report.status === "PUBLISHED" ? "已发布" : report.status}
          </span>
          <small className="mono">
            v{report.version} · {report.id.slice(0, 8)}
          </small>
          <QualityBadge quality={report.qualityStatus} />
        </div>
      </header>
      <div className="report-document">
        <section className="report-section">
          <div className="section-title">
            <div>
              <span className="section-number">01</span>
              <div>
                <h2>报告摘要</h2>
                <p>组合事实与市场解读的确定性摘要</p>
              </div>
            </div>
            {report.policyCheckStatus !== "PASSED" ? (
              <span className="status-pill warning">
                政策检查：{report.policyCheckStatus}
              </span>
            ) : null}
          </div>
          <ReportMarkdown>{report.summary}</ReportMarkdown>
        </section>

        <section className="report-section">
          <div className="section-title">
            <div>
              <span className="section-number">02</span>
              <div>
                <h2>条目明细</h2>
                <p>事实、风险、行动与估值条目（{report.items.length}）</p>
              </div>
            </div>
          </div>
          <div className="insight-grid">
            {report.items.map((item) => {
              const meta = ITEM_TYPE_META[item.itemType] ?? {
                label: item.itemType,
                icon: Info,
              };
              const Icon = meta.icon;
              return (
                <article className="insight-card" key={item.id}>
                  <div>
                    <span className="logic-tag fact">
                      <Icon size={10} />
                      {meta.label}
                    </span>
                    <QualityBadge quality={item.quality} />
                  </div>
                  <h3>{item.title}</h3>
                  <p>{item.body}</p>
                  {item.actionPlan ? (
                    <code className="action-plan-code">
                      {JSON.stringify(item.actionPlan)}
                    </code>
                  ) : null}
                  {item.evidence.length > 0 ? (
                    <button
                      className="evidence-link"
                      aria-label={`查看证据 ${item.evidence[0].id}`}
                      onClick={() => onEvidence(itemToEvidence(item, item.evidence[0]))}
                    >
                      <ExternalLink size={11} />
                      {evidenceLabel(report, item.evidence[0])}
                    </button>
                  ) : null}
                </article>
              );
            })}
          </div>
          {unlinkedItemCount > 0 ? (
            <p className="report-evidence-note">
              {unlinkedItemCount}条条目未关联源快照，已在可用条目中标注来源。
            </p>
          ) : null}
        </section>

        {report.modelRun ? (
          <section className="report-section">
            <div className="section-title">
              <div>
                <span className="section-number">03</span>
                <div>
                  <h2>模型运行</h2>
                  <p>生成过程的可复现标识</p>
                </div>
              </div>
            </div>
            <div className="model-run-meta">
              <span>
                <Sparkles size={12} /> {report.modelRun.provider} ·{" "}
                {report.modelRun.model}
              </span>
              <span>
                状态：{report.modelRun.status}
                {report.modelRun.errorClass
                  ? ` · ${report.modelRun.errorClass}`
                  : ""}
              </span>
              <span>
                tokens：{report.modelRun.tokenUsage?.prompt_tokens ?? 0} +{" "}
                {report.modelRun.tokenUsage?.completion_tokens ?? 0}
              </span>
              <span>成本：¥{report.modelRun.costCny}</span>
            </div>
          </section>
        ) : null}

        {report.versionHistory.length > 0 ? (
          <section className="report-section">
            <div className="section-title">
              <div>
                <span className="section-number">04</span>
                <div>
                  <h2>版本审计链</h2>
                  <p>同一时段报告的不可变版本与替代关系</p>
                </div>
              </div>
            </div>
            <div className="version-chain">
              <History size={14} />
              {report.versionHistory.map((version) => (
                <span key={version.id} className="version-chip mono">
                  v{version.version} · {version.status} ·{" "}
                  {formatShanghaiTime(version.asOf)}
                </span>
              ))}
              <span className="version-chip mono current">
                v{report.version} · 当前版本
              </span>
            </div>
          </section>
        ) : null}

        <div className="method-note">
          <Info size={15} />
          <div>
            <strong>证据与版本说明</strong>
            <p>
              只有已关联源快照的条目才展示证据入口；模型解释不会被伪装成
              外部事实。修订产生新版本并链接旧版本，旧版本永不删除。
            </p>
          </div>
        </div>
      </div>
      <footer className="report-disclaimer">
        本报告为个人投研辅助材料，不构成投资建议或自动交易指令。请结合风险政策与真实成交事实独立决策。
      </footer>
    </article>
  );
}

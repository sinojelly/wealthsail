import { getSessionLabel } from "../domain/reporting"
import type {
  Asset,
  InvestmentReport,
  Position,
  ReportSection,
  ReportSession,
  SourceEvidence,
} from "../domain/types"
import AssetTable from "./AssetTable"
import QualityBadge from "./QualityBadge"

interface ReportViewProps {
  report: InvestmentReport
  activeSession: ReportSession
  assets: Asset[]
  positions: Position[]
  evidence: SourceEvidence[]
  decisionConfirmed: boolean
  onSessionChange: (session: ReportSession) => void
  onAssetSelect: (asset: Asset) => void
  onConfirmDecision: () => void
}

const sessions: ReportSession[] = [
  "PRE_MARKET",
  "INTRADAY",
  "FUND_1430",
  "POST_MARKET",
]

const sectionTone: Record<ReportSection["tone"], string> = {
  POSITIVE: "border-l-emerald-400",
  WARNING: "border-l-amber-400",
  NEUTRAL: "border-l-slate-500",
  INFO: "border-l-cyan-400",
}

export default function ReportView({
  report,
  activeSession,
  assets,
  positions,
  evidence,
  decisionConfirmed,
  onSessionChange,
  onAssetSelect,
  onConfirmDecision,
}: ReportViewProps) {
  const reportEvidence = evidence.filter((item) =>
    report.evidenceIds?.includes(item.id),
  )
  const marketValue = positions.reduce((sum, item) => sum + item.marketValue, 0)
  const weightedPnl = positions.reduce(
    (sum, item) => sum + item.marketValue * item.pnlPercent,
    0,
  )
  const pnlPercent = marketValue ? weightedPnl / marketValue : 0

  return (
    <div className="mx-auto w-full max-w-6xl px-4 pb-24 pt-5 sm:px-7 sm:pb-10 sm:pt-7">
      <div
        aria-label="每日报告时段"
        className="no-scrollbar flex gap-1 overflow-x-auto border-b border-white/[0.07]"
        role="tablist"
      >
        {sessions.map((session) => (
          <button
            aria-pressed={activeSession === session}
            aria-selected={activeSession === session}
            className={`relative shrink-0 px-3 pb-3 pt-1 text-xs transition-colors sm:px-4 ${
              activeSession === session
                ? "text-slate-100"
                : "text-slate-500 hover:text-slate-300"
            }`}
            key={session}
            onClick={() => onSessionChange(session)}
            role="tab"
            type="button"
          >
            {getSessionLabel(session)}
            {activeSession === session && (
              <span className="absolute inset-x-3 bottom-0 h-px bg-emerald-400" />
            )}
          </button>
        ))}
      </div>

      <section className="grid gap-6 border-b border-white/[0.07] py-7 lg:grid-cols-[minmax(0,1fr)_250px]">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-emerald-400">
              {activeSession.replace("_", " · ")}
            </span>
            <QualityBadge quality={report.quality} />
          </div>
          <h1 className="mt-4 text-3xl font-semibold tracking-[-0.02em] text-white sm:text-4xl">
            {report.title}
          </h1>
          <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-400">
            {report.summary}
          </p>
          <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 font-mono text-[10px] text-slate-600">
            <span>生成 {report.generatedAt.slice(11, 16)}</span>
            <span>数据截止 {report.dataCutoffAt.slice(11, 16)}</span>
            <span>报告 ID {report.id}</span>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-px self-end bg-white/[0.07] lg:grid-cols-1">
          <div className="bg-[#091221] p-4">
            <p className="text-[10px] uppercase tracking-[0.16em] text-slate-600">
              组合市值
            </p>
            <p className="mt-2 font-mono text-xl text-slate-100">
              ¥
              {marketValue.toLocaleString("zh-CN", {
                maximumFractionDigits: 0,
              })}
            </p>
          </div>
          <div className="bg-[#091221] p-4">
            <p className="text-[10px] uppercase tracking-[0.16em] text-slate-600">
              持仓收益
            </p>
            <p
              className={`mt-2 font-mono text-xl ${
                pnlPercent >= 0 ? "text-emerald-400" : "text-rose-400"
              }`}
            >
              {pnlPercent >= 0 ? "+" : ""}
              {pnlPercent.toFixed(2)}%
            </p>
          </div>
        </div>
      </section>

      <section className="grid gap-px bg-white/[0.07] md:grid-cols-2">
        {report.sections.map((section) => (
          <article
            className={`border-l-2 bg-[#091221] p-5 sm:p-6 ${sectionTone[section.tone]}`}
            key={section.id}
          >
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-600">
              {section.eyebrow}
            </p>
            <h2 className="mt-3 text-base font-semibold text-slate-100">
              {section.title}
            </h2>
            <p className="mt-3 text-sm leading-6 text-slate-400">
              {section.body}
            </p>
          </article>
        ))}
      </section>

      {activeSession === "POST_MARKET" && (
        <section className="mt-6 flex flex-col justify-between gap-4 border border-white/[0.08] bg-white/[0.025] p-5 sm:flex-row sm:items-center">
          <div>
            <p className="text-xs font-medium text-slate-200">
              {decisionConfirmed ? "结论已确认并写入决策流水" : "待确认决策"}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              确认只记录研究结论，不会自动发起交易。
            </p>
          </div>
          <button
            className="border border-emerald-400/35 bg-emerald-400/10 px-4 py-2.5 text-xs font-medium text-emerald-300 transition-colors hover:bg-emerald-400/15 disabled:cursor-default disabled:opacity-50"
            disabled={decisionConfirmed}
            onClick={onConfirmDecision}
            type="button"
          >
            {decisionConfirmed ? "已确认" : "确认复盘结论"}
          </button>
        </section>
      )}

      <AssetTable
        assets={assets}
        onSelect={onAssetSelect}
        positions={positions}
        title="股票与基金一体化视图"
      />

      <section className="mt-6 border-t border-white/[0.07] pt-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-200">证据与来源</h2>
          <span className="text-[10px] text-slate-600">所有结论必须可回溯</span>
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {reportEvidence.map((source) => (
            <a
              className="group flex items-start justify-between border border-white/[0.07] p-4 transition-colors hover:border-white/[0.14]"
              href={source.url}
              key={source.id}
              rel="noreferrer"
              target="_blank"
            >
              <span>
                <span className="block text-xs font-medium text-slate-300">
                  {source.publisher}
                </span>
                <span className="mt-1 block text-[11px] leading-5 text-slate-500">
                  {source.title}
                </span>
              </span>
              <span className="text-slate-600 transition-colors group-hover:text-emerald-400">
                ↗
              </span>
            </a>
          ))}
        </div>
      </section>
    </div>
  )
}

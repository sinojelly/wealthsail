import { getSessionLabel } from "../domain/reporting"
import type { MainView, ReportSession } from "../domain/types"

interface SidebarProps {
  activeView: MainView
  activeSession: ReportSession
  onViewChange: (view: MainView) => void
  onSessionChange: (session: ReportSession) => void
}

interface NavigationItem {
  id: MainView
  label: string
  mark: string
}

const views: NavigationItem[] = [
  { id: "REPORTS", label: "今日研究", mark: "01" },
  { id: "PORTFOLIO", label: "组合持仓", mark: "02" },
  { id: "WATCHLIST", label: "观察清单", mark: "03" },
  { id: "SOURCES", label: "来源中心", mark: "04" },
]

const sessions: ReportSession[] = [
  "PRE_MARKET",
  "INTRADAY",
  "FUND_1430",
  "POST_MARKET",
]

export default function Sidebar({
  activeView,
  activeSession,
  onViewChange,
  onSessionChange,
}: SidebarProps) {
  return (
    <aside className="flex h-full w-[248px] shrink-0 flex-col border-r border-white/[0.07] bg-[#081121] px-5 py-6">
      <div className="flex items-center gap-3 px-1">
        <div className="grid h-9 w-9 place-items-center border border-emerald-400/30 bg-emerald-400/10 text-sm font-bold text-emerald-300">
          WS
        </div>
        <div>
          <p className="text-[15px] font-semibold tracking-wide text-white">
            WealthSail
          </p>
          <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">
            Research OS
          </p>
        </div>
      </div>

      <nav aria-label="主导航" className="mt-9 space-y-1">
        {views.map((view) => (
          <button
            className={`flex w-full items-center gap-3 px-2 py-2.5 text-left text-sm transition-colors ${
              activeView === view.id
                ? "bg-white/[0.06] text-slate-100"
                : "text-slate-500 hover:bg-white/[0.03] hover:text-slate-300"
            }`}
            key={view.id}
            onClick={() => onViewChange(view.id)}
            type="button"
          >
            <span className="font-mono text-[10px] text-slate-600">
              {view.mark}
            </span>
            <span>{view.label}</span>
            {activeView === view.id && (
              <span className="ml-auto h-1.5 w-1.5 rounded-full bg-emerald-400" />
            )}
          </button>
        ))}
      </nav>

      <div className="mt-8 border-t border-white/[0.07] pt-5">
        <p className="px-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-600">
          Daily rhythm
        </p>
        <div className="mt-3 space-y-1">
          {sessions.map((session, index) => (
            <button
              className={`flex w-full items-center gap-3 px-2 py-2 text-left text-xs transition-colors ${
                activeView === "REPORTS" && activeSession === session
                  ? "text-emerald-300"
                  : "text-slate-500 hover:text-slate-300"
              }`}
              key={session}
              onClick={() => {
                onViewChange("REPORTS")
                onSessionChange(session)
              }}
              type="button"
            >
              <span className="grid h-5 w-5 place-items-center rounded-full border border-white/10 font-mono text-[9px]">
                {index + 1}
              </span>
              {getSessionLabel(session)}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-auto border-t border-white/[0.07] pt-5">
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_12px_rgba(16,185,129,0.7)]" />
          数据服务正常
        </div>
        <p className="mt-3 text-[10px] leading-4 text-slate-600">
          演示数据 · 仅供研究辅助，不构成投资建议
        </p>
      </div>
    </aside>
  )
}

import { useMemo, useState } from "react"

import AppHeader from "./components/AppHeader"
import ContextPanel from "./components/ContextPanel"
import PortfolioView from "./components/PortfolioView"
import ReportView from "./components/ReportView"
import Sidebar from "./components/Sidebar"
import SourcesView from "./components/SourcesView"
import WatchlistView from "./components/WatchlistView"
import {
  assets,
  evidence,
  positions,
  reports,
  tenants,
  users,
  watchlistAssetIdsByTenant,
} from "./data/mockData"
import { getReportForTenant } from "./domain/reporting"
import type { Asset, MainView, ReportSession } from "./domain/types"

interface MobileViewItem {
  id: MainView
  label: string
  mark: string
}

const mobileViews: MobileViewItem[] = [
  { id: "REPORTS", label: "研究", mark: "R" },
  { id: "PORTFOLIO", label: "持仓", mark: "P" },
  { id: "WATCHLIST", label: "观察", mark: "W" },
  { id: "SOURCES", label: "来源", mark: "S" },
]

export default function App() {
  const [activeView, setActiveView] = useState<MainView>("REPORTS")
  const [activeSession, setActiveSession] =
    useState<ReportSession>("PRE_MARKET")
  const [activeUserId, setActiveUserId] = useState(users[0].id)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const [selectedAsset, setSelectedAsset] = useState<Asset | null>(null)
  const [decisionConfirmed, setDecisionConfirmed] = useState(false)

  const activeUser = users.find((user) => user.id === activeUserId) ?? users[0]
  const activeTenant =
    tenants.find((tenant) => tenant.id === activeUser.tenantId) ?? tenants[0]

  // The prototype filters every tenant-owned collection before rendering. In the
  // full system the API and PostgreSQL RLS enforce the same boundary independently.
  const tenantPositions = useMemo(
    () => positions.filter((position) => position.tenantId === activeTenant.id),
    [activeTenant.id],
  )
  const activeReport = getReportForTenant(
    reports,
    activeTenant.id,
    activeSession,
  )
  const watchlistAssets = (watchlistAssetIdsByTenant[activeTenant.id] ?? [])
    .map((assetId) => assets.find((asset) => asset.id === assetId))
    .filter((asset): asset is Asset => Boolean(asset))

  const selectSession = (session: ReportSession) => {
    setActiveSession(session)
    setActiveView("REPORTS")
    setDecisionConfirmed(false)
  }

  const selectUser = (userId: string) => {
    setActiveUserId(userId)
    setSelectedAsset(null)
    setDecisionConfirmed(false)
    setUserMenuOpen(false)
  }

  return (
    <div className="flex h-full min-h-0 overflow-hidden bg-[#060c1a] text-slate-200">
      <div className="hidden md:block">
        <Sidebar
          activeSession={activeSession}
          activeView={activeView}
          onSessionChange={selectSession}
          onViewChange={setActiveView}
        />
      </div>

      <div className="relative flex min-w-0 flex-1 flex-col">
        <AppHeader
          activeUser={activeUser}
          menuOpen={userMenuOpen}
          onSelectUser={selectUser}
          onToggleMenu={() => setUserMenuOpen((open) => !open)}
          tenants={tenants}
          users={users}
        />

        <main className="min-h-0 flex-1 overflow-y-auto">
          {activeView === "REPORTS" && activeReport && (
            <ReportView
              activeSession={activeSession}
              assets={assets}
              decisionConfirmed={decisionConfirmed}
              evidence={evidence}
              onAssetSelect={setSelectedAsset}
              onConfirmDecision={() => setDecisionConfirmed(true)}
              onSessionChange={selectSession}
              positions={tenantPositions}
              report={activeReport}
            />
          )}
          {activeView === "PORTFOLIO" && (
            <PortfolioView
              assets={assets}
              onAssetSelect={setSelectedAsset}
              positions={tenantPositions}
              tenant={activeTenant}
            />
          )}
          {activeView === "WATCHLIST" && (
            <WatchlistView
              assets={watchlistAssets}
              onAssetSelect={setSelectedAsset}
              tenant={activeTenant}
            />
          )}
          {activeView === "SOURCES" && <SourcesView evidence={evidence} />}
        </main>

        <nav
          aria-label="移动端导航"
          className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-4 border-t border-white/[0.08] bg-[#091425]/95 px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-2 backdrop-blur md:hidden"
        >
          {mobileViews.map((view) => (
            <button
              aria-label={view.label}
              className={`flex flex-col items-center gap-1 py-1 text-[10px] transition-colors ${
                activeView === view.id ? "text-emerald-300" : "text-slate-600"
              }`}
              key={view.id}
              onClick={() => setActiveView(view.id)}
              type="button"
            >
              <span className="grid h-6 w-6 place-items-center rounded-full border border-current font-mono text-[9px]">
                {view.mark}
              </span>
              {view.label}
            </button>
          ))}
        </nav>

        {selectedAsset && (
          <ContextPanel
            asset={selectedAsset}
            onClose={() => setSelectedAsset(null)}
          />
        )}
      </div>
    </div>
  )
}

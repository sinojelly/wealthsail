import type { Tenant, UserProfile } from "../domain/types"

interface AppHeaderProps {
  activeUser: UserProfile
  users: UserProfile[]
  tenants: Tenant[]
  menuOpen: boolean
  onToggleMenu: () => void
  onSelectUser: (userId: string) => void
}

function getTenantName(tenants: Tenant[], tenantId: string): string {
  return tenants.find((tenant) => tenant.id === tenantId)?.name ?? "未知账户"
}

export default function AppHeader({
  activeUser,
  users,
  tenants,
  menuOpen,
  onToggleMenu,
  onSelectUser,
}: AppHeaderProps) {
  return (
    <header className="relative flex h-[68px] shrink-0 items-center justify-between border-b border-white/[0.07] px-4 sm:px-7">
      <div>
        <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-600">
          China market · MVP workspace
        </p>
        <p className="mt-1 text-xs text-slate-400">最近交易日 2026-08-21</p>
      </div>

      <div className="flex items-center gap-3">
        <div className="hidden items-center gap-2 border-r border-white/[0.08] pr-4 text-xs sm:flex">
          <span
            className={`h-2 w-2 rounded-full ${
              activeUser.weComConnected ? "bg-emerald-400" : "bg-amber-400"
            }`}
          />
          <span className="text-slate-400">
            {activeUser.weComConnected ? "企业微信已连接" : "企业微信待连接"}
          </span>
        </div>

        <div className="relative">
          <button
            aria-expanded={menuOpen}
            aria-label="切换用户"
            className="flex items-center gap-3 px-1 py-1 text-left"
            onClick={onToggleMenu}
            type="button"
          >
            <span className="relative grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-emerald-400 to-cyan-500 text-xs font-bold text-[#06101d]">
              {activeUser.initials}
              <span
                aria-hidden="true"
                className={`absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full border-2 border-[#07101f] ${
                  activeUser.weComConnected ? "bg-emerald-400" : "bg-amber-400"
                }`}
              />
            </span>
            <span className="hidden sm:block">
              <span className="block text-xs font-medium text-slate-200">
                {activeUser.name}
              </span>
              <span className="mt-0.5 block text-[10px] text-slate-500">
                {getTenantName(tenants, activeUser.tenantId)}
              </span>
            </span>
            <span className="text-[10px] text-slate-600">⌄</span>
          </button>

          {menuOpen && (
            <div className="absolute right-0 top-12 z-50 w-64 border border-white/[0.09] bg-[#0d192d] p-2 shadow-2xl shadow-black/40">
              <p className="px-3 pb-2 pt-1 text-[10px] uppercase tracking-[0.18em] text-slate-600">
                演示用户 · 租户隔离
              </p>
              {users.map((user) => (
                <button
                  className={`flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-white/[0.05] ${
                    activeUser.id === user.id ? "bg-white/[0.04]" : ""
                  }`}
                  key={user.id}
                  onClick={() => onSelectUser(user.id)}
                  type="button"
                >
                  <span className="grid h-7 w-7 place-items-center rounded-full bg-slate-700 text-[11px] text-slate-200">
                    {user.initials}
                  </span>
                  <span>
                    <span className="block text-xs text-slate-200">
                      {user.name}
                    </span>
                    <span className="mt-0.5 block text-[10px] text-slate-500">
                      {getTenantName(tenants, user.tenantId)}
                    </span>
                  </span>
                  {activeUser.id === user.id && (
                    <span className="ml-auto text-xs text-emerald-400">
                      当前
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </header>
  )
}

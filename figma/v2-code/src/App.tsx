import { useState } from "react";
import Dashboard from "./pages/Dashboard";
import Reports from "./pages/Reports";
import RiskPolicy from "./pages/RiskPolicy";
import Settings from "./pages/Settings";
import Operations from "./pages/Operations";
import {
  LayoutDashboard,
  FileText,
  ShieldCheck,
  Settings2,
  Wrench,
  TrendingUp,
  Bell,
  LogOut,
  ChevronRight,
  Menu,
  X,
} from "lucide-react";

type Page = "dashboard" | "reports" | "risk-policy" | "settings" | "operations";

const NAV = [
  { id: "dashboard" as Page, label: "资产看板", icon: LayoutDashboard },
  { id: "reports" as Page, label: "研报审计", icon: FileText },
  { id: "risk-policy" as Page, label: "风控政策", icon: ShieldCheck },
  { id: "settings" as Page, label: "账号安全", icon: Settings2 },
  { id: "operations" as Page, label: "运维监控", icon: Wrench, admin: true },
];

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [role] = useState<"OWNER" | "ADMIN" | "EDITOR" | "VIEWER">("OWNER");

  const visibleNav = NAV.filter((n) => !n.admin || ["OWNER", "ADMIN"].includes(role));
  const current = NAV.find((n) => n.id === page)!;

  return (
    <div className="flex h-screen w-full overflow-hidden" style={{ background: "#F8FAFC" }}>
      {/* Sidebar */}
      <aside
        className="flex flex-col shrink-0 border-r transition-all duration-200"
        style={{
          width: sidebarOpen ? 220 : 64,
          borderColor: "#E2E8F0",
          background: "#0F172A",
        }}
      >
        {/* Logo */}
        <div
          className="flex items-center gap-3 px-4 border-b"
          style={{ height: 56, borderColor: "#1E293B" }}
        >
          <button
            className="shrink-0 flex items-center justify-center rounded transition-opacity hover:opacity-80"
            style={{ width: 32, height: 32, background: "#0D9488" }}
            onClick={() => setSidebarOpen(true)}
            title="展开侧边栏"
          >
            <TrendingUp size={16} color="#fff" />
          </button>
          {sidebarOpen && (
            <span className="font-semibold text-sm tracking-wide text-white">
              WealthSail
            </span>
          )}
          {sidebarOpen && (
            <button
              className="ml-auto p-1 rounded hover:bg-white/10 transition-colors"
              onClick={() => setSidebarOpen(false)}
              title="收起侧边栏"
            >
              <X size={14} color="#94A3B8" />
            </button>
          )}
        </div>

        {/* Nav */}
        <nav className="flex-1 py-4 flex flex-col gap-1 px-2">
          {visibleNav.map(({ id, label, icon: Icon }) => {
            const active = page === id;
            return (
              <button
                key={id}
                onClick={() => setPage(id)}
                className="flex items-center gap-3 px-3 py-2.5 rounded text-left w-full transition-all duration-150"
                style={{
                  background: active ? "#0D9488" : "transparent",
                  color: active ? "#fff" : "#94A3B8",
                }}
                onMouseEnter={(e) => {
                  if (!active)
                    (e.currentTarget as HTMLButtonElement).style.background = "#1E293B";
                }}
                onMouseLeave={(e) => {
                  if (!active)
                    (e.currentTarget as HTMLButtonElement).style.background = "transparent";
                }}
              >
                <Icon size={16} className="shrink-0" />
                {sidebarOpen && (
                  <span className="text-sm font-medium">{label}</span>
                )}
                {sidebarOpen && active && (
                  <ChevronRight size={12} className="ml-auto" />
                )}
              </button>
            );
          })}
        </nav>

        {/* User info */}
        <div
          className="border-t px-2 py-3"
          style={{ borderColor: "#1E293B" }}
        >
          <div className="flex items-center gap-3 px-3">
            <div
              className="shrink-0 rounded-full flex items-center justify-center text-xs font-semibold"
              style={{ width: 28, height: 28, background: "#0D9488", color: "#fff" }}
            >
              李
            </div>
            {sidebarOpen && (
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium text-white truncate">李明远</div>
                <div className="text-xs" style={{ color: "#475569" }}>
                  {role}
                </div>
              </div>
            )}
            {sidebarOpen && (
              <button className="p-1 rounded hover:bg-white/10">
                <LogOut size={13} color="#64748B" />
              </button>
            )}
          </div>
        </div>
      </aside>

      {/* Main area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Topbar */}
        <header
          className="shrink-0 flex items-center justify-between px-6 border-b bg-white"
          style={{ height: 56, borderColor: "#E2E8F0" }}
        >
          <div className="flex items-center gap-2">
            <span className="text-sm" style={{ color: "#94A3B8" }}>
              WealthSail
            </span>
            <ChevronRight size={12} style={{ color: "#CBD5E1" }} />
            <span className="text-sm font-medium" style={{ color: "#0F172A" }}>
              {current.label}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <span
              className="text-xs font-mono px-2 py-0.5 rounded"
              style={{ background: "#F1F5F9", color: "#475569" }}
            >
              Asia/Shanghai · 2026-08-23
            </span>
            <button
              className="relative p-2 rounded hover:bg-gray-100 transition-colors"
              title="通知"
            >
              <Bell size={16} style={{ color: "#475569" }} />
              <span
                className="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full"
                style={{ background: "#EF4444" }}
              />
            </button>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto">
          {page === "dashboard" && <Dashboard />}
          {page === "reports" && <Reports />}
          {page === "risk-policy" && <RiskPolicy />}
          {page === "settings" && <Settings role={role} />}
          {page === "operations" && <Operations />}
        </main>
      </div>
    </div>
  );
}

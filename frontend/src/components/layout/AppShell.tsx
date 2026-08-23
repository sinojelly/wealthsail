import { useState } from "react";
import {
  BarChart3,
  Bell,
  BookOpenCheck,
  ChevronLeft,
  ChevronRight,
  Menu,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { canViewOperations, fixtureSession } from "../../app/auth-session";

const navItems = [
  { to: "/dashboard", label: "资产看板", icon: BarChart3 },
  { to: "/reports", label: "研报审计", icon: BookOpenCheck },
  { to: "/risk-policy", label: "风控政策", icon: ShieldCheck },
  { to: "/settings", label: "账号安全", icon: Settings },
  {
    to: "/operations",
    label: "运维监控",
    icon: SlidersHorizontal,
    admin: true,
  },
];

const pageTitles: Record<string, string> = {
  "/dashboard": "资产看板",
  "/onboarding": "初始化导入",
  "/reports": "研报审计",
  "/risk-policy": "风控政策",
  "/settings": "账号安全",
  "/operations": "运维监控",
};

export default function AppShell() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const visibleItems = navItems.filter(
    (item) => !item.admin || canViewOperations(fixtureSession),
  );

  return (
    <div className={`app-shell ${collapsed ? "is-collapsed" : ""}`}>
      <aside
        className={`sidebar ${mobileOpen ? "mobile-open" : ""}`}
        aria-label="主导航"
      >
        <div className="brand-row">
          <div className="brand-mark">W</div>
          <div className="brand-copy">
            <strong>WealthSail</strong>
            <span>个人投研助手</span>
          </div>
          <button
            className="mobile-close"
            aria-label="关闭导航"
            onClick={() => setMobileOpen(false)}
          >
            <X size={18} />
          </button>
        </div>
        <nav className="nav-list">
          {visibleItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              aria-label={label}
              className={({ isActive }) =>
                `nav-item ${isActive ? "active" : ""}`
              }
              onClick={() => setMobileOpen(false)}
            >
              <Icon size={19} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="avatar">李</div>
          <div className="user-copy">
            <strong>{fixtureSession.displayName}</strong>
            <span>{fixtureSession.role}</span>
          </div>
        </div>
        <button
          className="collapse-button"
          aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
          onClick={() => setCollapsed((value) => !value)}
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </aside>
      {mobileOpen ? (
        <button
          className="mobile-scrim"
          aria-label="关闭导航遮罩"
          onClick={() => setMobileOpen(false)}
        />
      ) : null}
      <div className="content-shell">
        <header className="topbar">
          <button
            className="mobile-menu"
            aria-label="打开导航"
            onClick={() => setMobileOpen(true)}
          >
            <Menu size={20} />
          </button>
          <div className="breadcrumb">
            <span>工作台</span>
            <ChevronRight size={14} />
            <strong>{pageTitles[location.pathname] ?? "资产看板"}</strong>
          </div>
          <div className="topbar-meta">
            <span>2026年8月23日 · Asia/Shanghai</span>
            <button className="icon-button notification" aria-label="通知中心">
              <Bell size={18} />
              <i />
            </button>
          </div>
        </header>
        <main className="page-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

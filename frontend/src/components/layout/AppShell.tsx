import { useState } from "react";
import {
  BarChart3,
  Bell,
  BookOpenCheck,
  ChevronLeft,
  ChevronRight,
  Filter,
  Flame,
  LogOut,
  Menu,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { canViewOperations, logout, useAuthSession } from "../../app/auth-session";
import ChatDrawer from "../../features/chat/ChatDrawer";

const navItems = [
  { to: "/dashboard", label: "资产看板", icon: BarChart3 },
  { to: "/market-hot", label: "市场热点", icon: Flame },
  { to: "/fund-screener", label: "基金筛选", icon: Filter },
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
  "/market-hot": "市场热点",
  "/fund-screener": "基金筛选",
  "/reports": "研报审计",
  "/risk-policy": "风控政策",
  "/settings": "账号安全",
  "/operations": "运维监控",
};

export default function AppShell() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const session = useAuthSession();

  const visibleItems = navItems.filter(
    (item) => !item.admin || canViewOperations(session),
  );

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className={`app-shell ${collapsed ? "is-collapsed" : ""}`}>
      <aside
        className={`sidebar ${mobileOpen ? "mobile-open" : ""}`}
        aria-label="主导航"
      >
        <div className="brand-row" data-logo-failed="false">
          <img
            src="/logo.png?v=20260914m"
            alt="WealthSail"
            className="brand-logo-img"
            onError={(event) => {
              event.currentTarget.parentElement?.setAttribute(
                "data-logo-failed",
                "true",
              );
            }}
          />
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
          <div className="avatar">{session?.displayName.slice(0, 1) ?? "?"}</div>
          <div className="user-copy">
            <strong>{session?.displayName ?? "未登录"}</strong>
            <span>{session?.role ?? ""}</span>
          </div>
          <button
            className="icon-button logout-button"
            aria-label="退出登录"
            title="退出登录"
            onClick={handleLogout}
          >
            <LogOut size={15} />
          </button>
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
            <span>Asia/Shanghai</span>
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
      <ChatDrawer />
    </div>
  );
}

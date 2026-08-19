import { View, Channel } from "../data/mockData";

interface SidebarProps {
  activeView: View;
  activeChannel: Channel;
  onViewChange: (v: View) => void;
  onChannelChange: (c: Channel) => void;
  collapsed: boolean;
  onToggle: () => void;
}

const channels: { id: Channel; label: string; icon: string; badge?: number }[] =
  [
    { id: "premarket", label: "盘前推演", icon: "📊", badge: 1 },
    { id: "diagnosis", label: "个股诊断", icon: "🔍" },
  ];

const views: { id: View; icon: string; label: string }[] = [
  { id: "portfolio", icon: "▤", label: "持仓看板" },
  { id: "watchlist", icon: "★", label: "自选池" },
];

export default function Sidebar({
  activeView,
  activeChannel,
  onViewChange,
  onChannelChange,
  collapsed,
}: SidebarProps) {
  return (
    <aside
      className={`flex-shrink-0 flex flex-col transition-all duration-300 border-r bg-navy-900`}
      style={{
        width: collapsed ? "56px" : "220px",
        borderColor: "rgba(255,255,255,0.06)",
      }}
    >
      {/* Logo */}
      <div
        className="flex items-center gap-3 px-3 py-4 border-b"
        style={{ borderColor: "rgba(255,255,255,0.06)" }}
      >
        <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 text-base bg-emerald/15 border border-emerald/30">
          ⛵
        </div>
        {!collapsed && (
          <div>
            <div className="text-sm font-semibold text-slate-200 tracking-wide leading-none">
              WealthSail
            </div>
            <div
              className="text-[10px] mt-0.5 font-mono"
              style={{ color: "#10b981" }}
            >
              ● 实时连接
            </div>
          </div>
        )}
      </div>

      <nav className="flex-1 overflow-y-auto py-4 space-y-5">
        {/* Agent channels */}
        <div>
          {!collapsed && (
            <div className="px-3 mb-2 text-[10px] font-semibold uppercase tracking-widest text-slate-600">
              Agent 频道
            </div>
          )}
          {channels.map((ch) => {
            const isActive = activeView === "chat" && activeChannel === ch.id;
            return (
              <button
                key={ch.id}
                onClick={() => {
                  onViewChange("chat");
                  onChannelChange(ch.id);
                }}
                title={collapsed ? ch.label : undefined}
                className={`w-full flex items-center gap-2.5 py-2 px-3 text-sm transition-colors relative ${
                  isActive
                    ? "text-slate-200 bg-white/8"
                    : "text-slate-500 hover:text-slate-300 hover:bg-white/4"
                }`}
              >
                {isActive && (
                  <span
                    className="absolute left-0 top-1 bottom-1 w-0.5 rounded-r"
                    style={{ background: "#10b981" }}
                  />
                )}
                <span className="text-xs w-4 text-center flex-shrink-0">
                  {ch.icon}
                </span>
                {!collapsed && (
                  <>
                    <span className="flex-1 text-left">{ch.label}</span>
                    {ch.badge && (
                      <span
                        className="text-[10px] px-1.5 py-0.5 rounded-full font-mono leading-none"
                        style={{
                          background: "rgba(16,185,129,0.15)",
                          color: "#10b981",
                        }}
                      >
                        {ch.badge}
                      </span>
                    )}
                  </>
                )}
              </button>
            );
          })}
        </div>

        {/* Function views */}
        <div>
          {!collapsed && (
            <div className="px-3 mb-2 text-[10px] font-semibold uppercase tracking-widest text-slate-600">
              功能
            </div>
          )}
          {views.map((v) => {
            const isActive = activeView === v.id;
            return (
              <button
                key={v.id}
                onClick={() => onViewChange(v.id)}
                title={collapsed ? v.label : undefined}
                className={`w-full flex items-center gap-2.5 py-2 px-3 text-sm transition-colors relative ${
                  isActive
                    ? "text-slate-200 bg-white/8"
                    : "text-slate-500 hover:text-slate-300 hover:bg-white/4"
                }`}
              >
                {isActive && (
                  <span
                    className="absolute left-0 top-1 bottom-1 w-0.5 rounded-r"
                    style={{ background: "#10b981" }}
                  />
                )}
                <span className="text-xs w-4 text-center flex-shrink-0">
                  {v.icon}
                </span>
                {!collapsed && <span className="flex-1 text-left">{v.label}</span>}
              </button>
            );
          })}
        </div>
      </nav>

      {/* Agent avatar */}
      <div
        className="px-3 py-3 border-t flex items-center gap-2.5"
        style={{ borderColor: "rgba(255,255,255,0.06)" }}
      >
        <div
          className="w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center text-sm border"
          style={{
            background: "rgba(34,211,238,0.08)",
            borderColor: "rgba(34,211,238,0.2)",
          }}
        >
          🤖
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <div className="text-xs font-medium text-slate-300 truncate">
              AlphaGhost
            </div>
            <div
              className="text-[10px] font-mono"
              style={{ color: "#10b981" }}
            >
              ● 在线
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

import { useState } from "react";
import Sidebar from "./components/Sidebar";
import ChatArea from "./components/ChatArea";
import ContextPanel from "./components/ContextPanel";
import PortfolioView from "./components/PortfolioView";
import WatchlistView from "./components/WatchlistView";
import { View, Channel, StockData } from "./data/mockData";

export default function App() {
  const [view, setView] = useState<View>("chat");
  const [channel, setChannel] = useState<Channel>("premarket");
  const [focusedStock, setFocusedStock] = useState<StockData | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileView, setMobileView] = useState<View>("chat");

  const activeView = view;

  const handleStockFocus = (stock: StockData) => {
    setFocusedStock(stock);
  };

  return (
    <div
      className="flex h-full overflow-hidden"
      style={{ background: "#060c1a", color: "#e2e8f0" }}
    >
      {/* Sidebar — hidden on mobile */}
      <div className="hidden md:flex">
        <Sidebar
          activeView={activeView}
          activeChannel={channel}
          onViewChange={(v) => { setView(v); if (v === "chat") {} }}
          onChannelChange={setChannel}
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed((c) => !c)}
        />
      </div>

      {/* Sidebar collapse toggle — desktop only */}
      <button
        className="hidden md:flex absolute left-0 top-1/2 -translate-y-1/2 z-10 w-4 h-8 items-center justify-center rounded-r transition-all hover:bg-white/10"
        style={{
          background: "rgba(255,255,255,0.04)",
          left: sidebarCollapsed ? "56px" : "220px",
          color: "#475569",
          fontSize: "8px",
        }}
        onClick={() => setSidebarCollapsed((c) => !c)}
      >
        {sidebarCollapsed ? "›" : "‹"}
      </button>

      {/* Main content */}
      <div className="flex-1 flex min-w-0 overflow-hidden">
        {activeView === "chat" && (
          <ChatArea channel={channel} onStockFocus={handleStockFocus} />
        )}
        {activeView === "portfolio" && <PortfolioView />}
        {activeView === "watchlist" && (
          <WatchlistView onStockFocus={handleStockFocus} />
        )}
      </div>

      {/* Right context panel */}
      {(activeView === "chat" || activeView === "watchlist") && (
        <ContextPanel
          stock={focusedStock}
          onClose={() => setFocusedStock(null)}
        />
      )}

      {/* Mobile bottom nav */}
      <nav
        className="md:hidden fixed bottom-0 left-0 right-0 flex border-t z-50"
        style={{
          background: "#0c1528",
          borderColor: "rgba(255,255,255,0.08)",
        }}
      >
        {[
          { id: "chat" as View, icon: "💬", label: "对话" },
          { id: "portfolio" as View, icon: "▤", label: "持仓" },
          { id: "watchlist" as View, icon: "★", label: "自选" },
        ].map((item) => (
          <button
            key={item.id}
            onClick={() => setView(item.id)}
            className="flex-1 flex flex-col items-center py-3 gap-1 transition-colors"
            style={{
              color: activeView === item.id ? "#10b981" : "#475569",
            }}
          >
            <span className="text-lg leading-none">{item.icon}</span>
            <span className="text-[10px]">{item.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}

import { watchlist, StockData, Sentiment } from "../data/mockData";
import Sparkline from "./Sparkline";

const sentimentMap: Record<Sentiment, { label: string; color: string }> = {
  bullish: { label: "偏暖", color: "#10b981" },
  bearish: { label: "防御", color: "#f43f5e" },
  neutral: { label: "中性", color: "#f59e0b" },
};

interface WatchlistViewProps {
  onStockFocus: (stock: StockData) => void;
}

export default function WatchlistView({ onStockFocus }: WatchlistViewProps) {
  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div
        className="px-6 py-4 border-b flex-shrink-0 flex items-center justify-between"
        style={{ borderColor: "rgba(255,255,255,0.06)" }}
      >
        <div>
          <div className="text-sm font-semibold text-slate-200">自选池</div>
          <div className="text-[10px] text-slate-600 font-mono mt-0.5">
            Watchlist · {watchlist.length} 只标的
          </div>
        </div>
        <button
          className="px-3 py-1.5 text-xs rounded-lg border transition-colors hover:bg-white/5"
          style={{ color: "#94a3b8", borderColor: "rgba(255,255,255,0.1)" }}
        >
          + 添加
        </button>
      </div>

      {/* Market summary bar */}
      <div
        className="px-6 py-3 flex gap-6 border-b overflow-x-auto flex-shrink-0"
        style={{ borderColor: "rgba(255,255,255,0.05)" }}
      >
        {[
          { label: "恒生指数", value: "20,842", change: "+1.24%" },
          { label: "上证综指", value: "3,412", change: "+0.58%" },
          { label: "深证成指", value: "10,243", change: "+0.81%" },
          { label: "中概股ETF", value: "23.45", change: "+2.18%" },
        ].map((idx) => (
          <div key={idx.label} className="flex-shrink-0">
            <div className="text-[10px] text-slate-600 mb-0.5">{idx.label}</div>
            <div className="flex items-baseline gap-1.5">
              <span className="text-xs font-mono text-slate-300">{idx.value}</span>
              <span className="text-[10px] font-mono" style={{ color: "#10b981" }}>
                {idx.change}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Stock list */}
      <div className="flex-1 overflow-y-auto">
        {watchlist.map((s, i) => {
          const pos = s.changePercent >= 0;
          const sent = sentimentMap[s.sentiment];
          return (
            <button
              key={s.symbol}
              onClick={() => onStockFocus(s)}
              className="w-full px-6 py-4 flex items-center gap-4 transition-colors hover:bg-white/3 text-left"
              style={{
                borderBottom: "1px solid rgba(255,255,255,0.04)",
              }}
            >
              {/* Name & symbol */}
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-slate-200">
                  {s.name}
                </div>
                <div className="text-[10px] font-mono text-slate-600 mt-0.5">
                  {s.exchange}:{s.symbol}
                </div>
              </div>

              {/* Sparkline */}
              <div className="flex-shrink-0">
                <Sparkline data={s.sparkline} width={72} height={28} positive={pos} />
              </div>

              {/* Price */}
              <div className="text-right flex-shrink-0 w-24">
                <div
                  className="text-sm font-mono font-semibold"
                  style={{ color: pos ? "#10b981" : "#f43f5e" }}
                >
                  ¥{s.price.toFixed(2)}
                </div>
                <div
                  className="text-[10px] font-mono mt-0.5"
                  style={{
                    color: pos
                      ? "rgba(16,185,129,0.7)"
                      : "rgba(244,63,94,0.7)",
                  }}
                >
                  {pos ? "+" : ""}
                  {s.changePercent.toFixed(2)}%
                </div>
              </div>

              {/* Sentiment */}
              <div className="flex-shrink-0 w-12 text-right">
                <span
                  className="text-[10px] font-semibold"
                  style={{ color: sent.color }}
                >
                  {sent.label}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

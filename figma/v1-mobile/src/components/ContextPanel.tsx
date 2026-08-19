import { StockData, portfolio } from "../data/mockData";
import Sparkline from "./Sparkline";

interface ContextPanelProps {
  stock: StockData | null;
  onClose: () => void;
}

export default function ContextPanel({ stock, onClose }: ContextPanelProps) {
  if (!stock) {
    return (
      <div
        className="hidden lg:flex w-[260px] flex-shrink-0 flex-col border-l items-center justify-center"
        style={{
          borderColor: "rgba(255,255,255,0.06)",
          background: "#0c1528",
        }}
      >
        <div className="text-center px-6">
          <div className="text-2xl mb-3 opacity-30">📈</div>
          <div className="text-xs text-slate-600 leading-relaxed">
            点击股票行查看详情与持仓信息
          </div>
        </div>
      </div>
    );
  }

  const pos = stock.changePercent >= 0;
  const position = portfolio.find((p) => p.stock.symbol === stock.symbol);
  const pnl = position
    ? (stock.price - position.costBasis) * position.shares
    : null;
  const pnlPct = position
    ? ((stock.price - position.costBasis) / position.costBasis) * 100
    : null;

  const priceRange = stock.high52w - stock.low52w;
  const pricePos = ((stock.price - stock.low52w) / priceRange) * 100;

  return (
    <div
      className="hidden lg:flex w-[260px] flex-shrink-0 flex-col border-l overflow-y-auto"
      style={{
        borderColor: "rgba(255,255,255,0.06)",
        background: "#0c1528",
      }}
    >
      {/* Header */}
      <div
        className="px-4 py-4 border-b flex items-start justify-between"
        style={{ borderColor: "rgba(255,255,255,0.06)" }}
      >
        <div>
          <div className="text-sm font-semibold text-slate-200">
            {stock.name}
          </div>
          <div className="text-[10px] text-slate-600 font-mono mt-0.5">
            {stock.exchange}:{stock.symbol}
          </div>
        </div>
        <button
          onClick={onClose}
          className="text-slate-600 hover:text-slate-400 transition-colors text-xs w-5 h-5 flex items-center justify-center"
        >
          ✕
        </button>
      </div>

      {/* Price & chart */}
      <div className="px-4 pt-4 pb-3">
        <div className="flex items-end justify-between mb-1">
          <div
            className="text-2xl font-mono font-bold"
            style={{ color: pos ? "#10b981" : "#f43f5e" }}
          >
            ¥{stock.price.toFixed(2)}
          </div>
          <div
            className="text-xs font-mono pb-1"
            style={{ color: pos ? "rgba(16,185,129,0.8)" : "rgba(244,63,94,0.8)" }}
          >
            {pos ? "+" : ""}
            {stock.change.toFixed(2)} ({pos ? "+" : ""}
            {stock.changePercent.toFixed(2)}%)
          </div>
        </div>
        <div className="mt-3">
          <Sparkline
            data={stock.sparkline}
            width={228}
            height={56}
            positive={pos}
          />
        </div>
        <div className="flex justify-between mt-1">
          <span className="text-[10px] text-slate-700 font-mono">5日走势</span>
          <span className="text-[10px] text-slate-600 font-mono">
            成交量 {stock.volume}
          </span>
        </div>
      </div>

      {/* 52W range */}
      <div
        className="px-4 py-3 border-t border-b"
        style={{ borderColor: "rgba(255,255,255,0.05)" }}
      >
        <div className="text-[10px] text-slate-600 uppercase tracking-wider mb-2">
          52周区间
        </div>
        <div className="relative h-1 rounded-full mb-2" style={{ background: "rgba(255,255,255,0.08)" }}>
          <div
            className="absolute top-0 h-1 rounded-full"
            style={{
              left: 0,
              width: `${pricePos}%`,
              background: pos ? "#10b981" : "#f43f5e",
            }}
          />
          <div
            className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full border-2"
            style={{
              left: `calc(${pricePos}% - 5px)`,
              background: "#060c1a",
              borderColor: pos ? "#10b981" : "#f43f5e",
            }}
          />
        </div>
        <div className="flex justify-between text-[10px] font-mono">
          <span className="text-slate-600">
            ¥{stock.low52w.toFixed(2)}
          </span>
          <span className="text-slate-600">
            ¥{stock.high52w.toFixed(2)}
          </span>
        </div>
      </div>

      {/* Support / Resistance */}
      <div
        className="px-4 py-3 border-b"
        style={{ borderColor: "rgba(255,255,255,0.05)" }}
      >
        <div className="text-[10px] text-slate-600 uppercase tracking-wider mb-2">
          关键价位
        </div>
        <div className="space-y-2">
          <div className="flex justify-between items-center">
            <span
              className="text-[10px] px-2 py-0.5 rounded font-semibold"
              style={{ background: "rgba(244,63,94,0.1)", color: "#f43f5e" }}
            >
              压力
            </span>
            <span className="text-xs font-mono text-slate-300">
              ¥{stock.resistance.toFixed(2)}
            </span>
          </div>
          <div
            className="text-[10px] font-mono text-center text-slate-700"
            style={{ color: pos ? "#10b981" : "#f43f5e" }}
          >
            ▲ 现价 ¥{stock.price.toFixed(2)}
          </div>
          <div className="flex justify-between items-center">
            <span
              className="text-[10px] px-2 py-0.5 rounded font-semibold"
              style={{ background: "rgba(16,185,129,0.1)", color: "#10b981" }}
            >
              支撑
            </span>
            <span className="text-xs font-mono text-slate-300">
              ¥{stock.support.toFixed(2)}
            </span>
          </div>
        </div>
      </div>

      {/* Key stats */}
      <div
        className="px-4 py-3 border-b"
        style={{ borderColor: "rgba(255,255,255,0.05)" }}
      >
        <div className="text-[10px] text-slate-600 uppercase tracking-wider mb-2">
          基本指标
        </div>
        <div className="space-y-2">
          {[
            { label: "市盈率", value: `${stock.pe}x` },
            { label: "成交量", value: stock.volume },
            { label: "市场", value: stock.exchange === "HK" ? "港股" : stock.exchange === "SH" ? "沪市" : "深市" },
          ].map((item) => (
            <div key={item.label} className="flex justify-between">
              <span className="text-[11px] text-slate-600">{item.label}</span>
              <span className="text-[11px] font-mono text-slate-300">
                {item.value}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* My position */}
      {position ? (
        <div className="px-4 py-3">
          <div className="text-[10px] text-slate-600 uppercase tracking-wider mb-2">
            我的持仓
          </div>
          <div
            className="rounded-xl p-3 border"
            style={{
              background: (pnl ?? 0) >= 0 ? "rgba(16,185,129,0.06)" : "rgba(244,63,94,0.06)",
              borderColor: (pnl ?? 0) >= 0 ? "rgba(16,185,129,0.15)" : "rgba(244,63,94,0.15)",
            }}
          >
            <div className="space-y-1.5">
              {[
                { label: "持股数量", value: `${position.shares} 股` },
                { label: "买入均价", value: `¥${position.costBasis.toFixed(2)}` },
                {
                  label: "浮动盈亏",
                  value: `${(pnl ?? 0) >= 0 ? "+" : ""}¥${pnl?.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`,
                  color: (pnl ?? 0) >= 0 ? "#10b981" : "#f43f5e",
                },
                {
                  label: "收益率",
                  value: `${(pnlPct ?? 0) >= 0 ? "+" : ""}${pnlPct?.toFixed(2)}%`,
                  color: (pnlPct ?? 0) >= 0 ? "#10b981" : "#f43f5e",
                },
              ].map((item) => (
                <div key={item.label} className="flex justify-between">
                  <span className="text-[11px] text-slate-600">{item.label}</span>
                  <span
                    className="text-[11px] font-mono"
                    style={{ color: item.color ?? "#94a3b8" }}
                  >
                    {item.value}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="px-4 py-3">
          <div className="text-[10px] text-slate-600 uppercase tracking-wider mb-2">
            我的持仓
          </div>
          <div className="text-xs text-slate-700 text-center py-4">
            未持有该股票
          </div>
        </div>
      )}
    </div>
  );
}

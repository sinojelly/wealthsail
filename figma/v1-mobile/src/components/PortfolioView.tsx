import { portfolio } from "../data/mockData";
import Sparkline from "./Sparkline";

export default function PortfolioView() {
  const totalCost = portfolio.reduce(
    (sum, p) => sum + p.costBasis * p.shares,
    0
  );
  const totalValue = portfolio.reduce(
    (sum, p) => sum + p.stock.price * p.shares,
    0
  );
  const totalPnl = totalValue - totalCost;
  const totalPct = (totalPnl / totalCost) * 100;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div
        className="px-6 py-4 border-b flex-shrink-0"
        style={{ borderColor: "rgba(255,255,255,0.06)" }}
      >
        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold text-slate-200">
              持仓看板
            </div>
            <div className="text-[10px] text-slate-600 font-mono mt-0.5">
              Portfolio · 实时同步
            </div>
          </div>
          <button
            className="px-3 py-1.5 text-xs rounded-lg border transition-colors hover:bg-emerald/10"
            style={{ color: "#10b981", borderColor: "rgba(16,185,129,0.3)" }}
          >
            + 添加持仓
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
        {/* Summary tiles */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            {
              label: "总市值",
              value: `¥${totalValue.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`,
              color: "#94a3b8",
            },
            {
              label: "总成本",
              value: `¥${totalCost.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`,
              color: "#64748b",
            },
            {
              label: "浮动盈亏",
              value: `${totalPnl >= 0 ? "+" : ""}¥${totalPnl.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`,
              color: totalPnl >= 0 ? "#10b981" : "#f43f5e",
            },
            {
              label: "总收益率",
              value: `${totalPct >= 0 ? "+" : ""}${totalPct.toFixed(2)}%`,
              color: totalPct >= 0 ? "#10b981" : "#f43f5e",
            },
          ].map((tile) => (
            <div
              key={tile.label}
              className="rounded-xl p-4 border"
              style={{
                background: "#0e1b32",
                borderColor: "rgba(255,255,255,0.07)",
              }}
            >
              <div className="text-[10px] text-slate-600 uppercase tracking-wider mb-1.5">
                {tile.label}
              </div>
              <div
                className="text-lg font-mono font-semibold leading-none"
                style={{ color: tile.color }}
              >
                {tile.value}
              </div>
            </div>
          ))}
        </div>

        {/* Holdings table */}
        <div
          className="rounded-xl border overflow-hidden"
          style={{
            background: "#0e1b32",
            borderColor: "rgba(255,255,255,0.07)",
          }}
        >
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr
                  style={{
                    borderBottom: "1px solid rgba(255,255,255,0.05)",
                  }}
                >
                  {[
                    "标的",
                    "持股 / 成本",
                    "现价",
                    "市值",
                    "浮动盈亏",
                    "走势",
                    "操作",
                  ].map((h) => (
                    <th
                      key={h}
                      className="px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-wider text-slate-600"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {portfolio.map((p, i) => {
                  const pnl = (p.stock.price - p.costBasis) * p.shares;
                  const pnlPct =
                    ((p.stock.price - p.costBasis) / p.costBasis) * 100;
                  const pos = p.stock.changePercent >= 0;
                  const pnlPos = pnl >= 0;

                  return (
                    <tr
                      key={p.stock.symbol}
                      className="hover:bg-white/3 transition-colors"
                      style={{
                        borderBottom:
                          i < portfolio.length - 1
                            ? "1px solid rgba(255,255,255,0.04)"
                            : "none",
                      }}
                    >
                      <td className="px-5 py-4">
                        <div className="text-sm font-medium text-slate-200">
                          {p.stock.name}
                        </div>
                        <div className="text-[10px] font-mono text-slate-600">
                          {p.stock.exchange}:{p.stock.symbol}
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <div className="text-sm font-mono text-slate-300">
                          {p.shares.toLocaleString()} 股
                        </div>
                        <div className="text-[10px] font-mono text-slate-600">
                          成本 ¥{p.costBasis.toFixed(2)}
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <div
                          className="text-sm font-mono font-semibold"
                          style={{ color: pos ? "#10b981" : "#f43f5e" }}
                        >
                          ¥{p.stock.price.toFixed(2)}
                        </div>
                        <div
                          className="text-[10px] font-mono"
                          style={{
                            color: pos
                              ? "rgba(16,185,129,0.7)"
                              : "rgba(244,63,94,0.7)",
                          }}
                        >
                          {pos ? "+" : ""}
                          {p.stock.changePercent.toFixed(2)}%
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <div className="text-sm font-mono text-slate-300">
                          ¥
                          {(p.stock.price * p.shares).toLocaleString("zh-CN", {
                            maximumFractionDigits: 0,
                          })}
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <div
                          className="text-sm font-mono font-semibold"
                          style={{ color: pnlPos ? "#10b981" : "#f43f5e" }}
                        >
                          {pnlPos ? "+" : ""}¥
                          {Math.abs(pnl).toLocaleString("zh-CN", {
                            maximumFractionDigits: 0,
                          })}
                        </div>
                        <div
                          className="text-[10px] font-mono"
                          style={{
                            color: pnlPos
                              ? "rgba(16,185,129,0.7)"
                              : "rgba(244,63,94,0.7)",
                          }}
                        >
                          {pnlPos ? "+" : ""}
                          {pnlPct.toFixed(2)}%
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <Sparkline
                          data={p.stock.sparkline}
                          width={60}
                          height={24}
                          positive={pos}
                        />
                      </td>
                      <td className="px-5 py-4">
                        <div className="flex gap-2">
                          <button
                            className="px-2.5 py-1 text-[11px] rounded border transition-colors hover:bg-emerald/10"
                            style={{
                              color: "#10b981",
                              borderColor: "rgba(16,185,129,0.25)",
                            }}
                          >
                            买入
                          </button>
                          <button
                            className="px-2.5 py-1 text-[11px] rounded border transition-colors hover:bg-rose/10"
                            style={{
                              color: "#f43f5e",
                              borderColor: "rgba(244,63,94,0.25)",
                            }}
                          >
                            卖出
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Allocation pie placeholder */}
        <div
          className="rounded-xl border p-5"
          style={{
            background: "#0e1b32",
            borderColor: "rgba(255,255,255,0.07)",
          }}
        >
          <div className="text-xs font-semibold text-slate-500 mb-4 uppercase tracking-wider">
            仓位分布
          </div>
          <div className="flex gap-6 items-center">
            <div className="flex-shrink-0">
              <AllocationRing data={portfolio} totalValue={totalValue} />
            </div>
            <div className="space-y-2">
              {portfolio.map((p, i) => {
                const weight =
                  ((p.stock.price * p.shares) / totalValue) * 100;
                const colors = ["#10b981", "#22d3ee", "#f43f5e", "#f59e0b"];
                return (
                  <div key={p.stock.symbol} className="flex items-center gap-2">
                    <div
                      className="w-2 h-2 rounded-full flex-shrink-0"
                      style={{ background: colors[i % colors.length] }}
                    />
                    <span className="text-xs text-slate-400">
                      {p.stock.name}
                    </span>
                    <span className="text-xs font-mono text-slate-600 ml-auto">
                      {weight.toFixed(1)}%
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function AllocationRing({
  data,
  totalValue,
}: {
  data: typeof portfolio;
  totalValue: number;
}) {
  const colors = ["#10b981", "#22d3ee", "#f43f5e", "#f59e0b"];
  const r = 40;
  const cx = 56;
  const cy = 56;
  const circumference = 2 * Math.PI * r;

  let offset = 0;
  const slices = data.map((p, i) => {
    const weight = (p.stock.price * p.shares) / totalValue;
    const dash = weight * circumference;
    const gap = circumference - dash;
    const slice = { offset, dash, gap, color: colors[i % colors.length] };
    offset += dash;
    return slice;
  });

  return (
    <svg width={112} height={112} viewBox="0 0 112 112">
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill="none"
        stroke="rgba(255,255,255,0.05)"
        strokeWidth="14"
      />
      {slices.map((s, i) => (
        <circle
          key={i}
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke={s.color}
          strokeWidth="14"
          strokeDasharray={`${s.dash} ${s.gap}`}
          strokeDashoffset={-s.offset + circumference * 0.25}
          strokeLinecap="butt"
          style={{ transform: "rotate(-90deg)", transformOrigin: `${cx}px ${cy}px` }}
        />
      ))}
    </svg>
  );
}

import { useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from "recharts";
import {
  TrendingUp,
  TrendingDown,
  DollarSign,
  BarChart2,
  RefreshCw,
  Plus,
  AlertCircle,
  ChevronDown,
  Filter,
  ArrowUpDown,
  CheckCircle2,
  Clock,
} from "lucide-react";

const equityData = [
  { date: "01/05", portfolio: 100.0, benchmark: 100.0 },
  { date: "02/05", portfolio: 101.2, benchmark: 100.4 },
  { date: "03/05", portfolio: 103.1, benchmark: 101.1 },
  { date: "06/05", portfolio: 102.3, benchmark: 101.5 },
  { date: "07/05", portfolio: 104.8, benchmark: 102.0 },
  { date: "08/05", portfolio: 106.2, benchmark: 102.7 },
  { date: "09/05", portfolio: 105.1, benchmark: 103.1 },
  { date: "10/05", portfolio: 107.4, benchmark: 103.8 },
  { date: "13/05", portfolio: 109.3, benchmark: 104.2 },
  { date: "14/05", portfolio: 108.1, benchmark: 104.5 },
  { date: "15/05", portfolio: 111.2, benchmark: 105.0 },
  { date: "16/05", portfolio: 110.4, benchmark: 105.4 },
  { date: "20/05", portfolio: 113.7, benchmark: 106.1 },
  { date: "21/05", portfolio: 115.0, benchmark: 106.6 },
  { date: "22/05", portfolio: 113.9, benchmark: 107.0 },
  { date: "27/05", portfolio: 116.8, benchmark: 107.8 },
  { date: "28/05", portfolio: 118.3, benchmark: 108.2 },
  { date: "29/05", portfolio: 117.2, benchmark: 108.5 },
  { date: "30/05", portfolio: 119.4, benchmark: 109.0 },
  { date: "03/06", portfolio: 121.1, benchmark: 109.3 },
];

const allocationData = [
  { name: "A 股股票", value: 48.3, color: "#0D9488" },
  { name: "场内 ETF", value: 18.7, color: "#0EA5E9" },
  { name: "场外基金", value: 22.5, color: "#8B5CF6" },
  { name: "现金", value: 10.5, color: "#94A3B8" },
];

const holdings = [
  {
    code: "600519",
    name: "贵州茅台",
    type: "A 股",
    shares: "200",
    cost: "1,580.00",
    price: "1,712.50",
    value: "342,500.00",
    pnlPct: "+8.39%",
    pnlAmt: "+26,500.00",
    weight: "17.8%",
    asOf: "2026-08-23 15:00",
    quality: "FRESH",
  },
  {
    code: "300750",
    name: "宁德时代",
    type: "A 股",
    shares: "500",
    cost: "198.40",
    price: "213.80",
    value: "106,900.00",
    pnlPct: "+7.76%",
    pnlAmt: "+7,700.00",
    weight: "5.6%",
    asOf: "2026-08-23 15:00",
    quality: "FRESH",
  },
  {
    code: "159919",
    name: "沪深300 ETF",
    type: "场内 ETF",
    shares: "10,000",
    cost: "3.512",
    price: "3.764",
    value: "37,640.00",
    pnlPct: "+7.17%",
    pnlAmt: "+2,520.00",
    weight: "2.0%",
    asOf: "2026-08-23 15:00",
    quality: "FRESH",
  },
  {
    code: "007540",
    name: "易方达蓝筹精选",
    type: "场外基金",
    shares: "15,280",
    cost: "1.3420",
    price: "~1.4180",
    value: "~216,670.00",
    pnlPct: "~+5.66%",
    pnlAmt: "~+11,590.00",
    weight: "11.3%",
    asOf: "2026-08-23 14:30",
    quality: "ESTIMATED",
  },
  {
    code: "001938",
    name: "中欧医疗健康",
    type: "场外基金",
    shares: "8,900",
    cost: "2.1050",
    price: "~2.2340",
    value: "~198,826.00",
    pnlPct: "~+6.13%",
    pnlAmt: "~+11,481.00",
    weight: "10.4%",
    asOf: "2026-08-23 14:30",
    quality: "ESTIMATED",
  },
  {
    code: "CASH",
    name: "人民币现金",
    type: "现金",
    shares: "—",
    cost: "—",
    price: "1.0000",
    value: "201,382.00",
    pnlPct: "—",
    pnlAmt: "—",
    weight: "10.5%",
    asOf: "2026-08-23 12:00",
    quality: "FRESH",
  },
];

const pendingFills = [
  {
    id: "F2026082301",
    code: "002230",
    name: "科大讯飞",
    direction: "BUY",
    shares: "1,000",
    price: "43.28",
    fee: "43.28",
    source: "企业微信草稿",
    time: "09:32",
  },
  {
    id: "F2026082302",
    code: "600036",
    name: "招商银行",
    direction: "SELL",
    shares: "500",
    price: "38.82",
    fee: "19.41",
    source: "手动录入",
    time: "14:17",
  },
];

function QualityBadge({ q }: { q: string }) {
  const map: Record<string, string> = {
    FRESH: "badge-fresh",
    ESTIMATED: "badge-estimated",
    STALE: "badge-stale",
    UNAVAILABLE: "badge-unavailable",
  };
  const label: Record<string, string> = {
    FRESH: "实时",
    ESTIMATED: "估算",
    STALE: "陈旧",
    UNAVAILABLE: "不可用",
  };
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${map[q] || "badge-unavailable"}`}>
      {label[q] || q}
    </span>
  );
}

function MetricCard({
  label,
  value,
  sub,
  trend,
  quality,
  asOf,
}: {
  label: string;
  value: string;
  sub?: string;
  trend?: "up" | "down" | "neutral";
  quality: string;
  asOf: string;
}) {
  return (
    <div
      className="bg-white rounded-lg border p-5"
      style={{ borderColor: "#E2E8F0" }}
    >
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-medium uppercase tracking-wider" style={{ color: "#64748B" }}>
          {label}
        </span>
        <QualityBadge q={quality} />
      </div>
      <div
        className="text-2xl font-semibold font-mono mb-1"
        style={{ color: "#0F172A", letterSpacing: "-0.02em" }}
      >
        {value}
      </div>
      {sub && (
        <div className="flex items-center gap-1.5">
          {trend === "up" && <TrendingUp size={13} style={{ color: "#10B981" }} />}
          {trend === "down" && <TrendingDown size={13} style={{ color: "#EF4444" }} />}
          <span
            className="text-sm font-mono"
            style={{
              color: trend === "up" ? "#10B981" : trend === "down" ? "#EF4444" : "#64748B",
            }}
          >
            {sub}
          </span>
        </div>
      )}
      <div className="mt-2 text-xs font-mono" style={{ color: "#94A3B8" }}>
        as_of {asOf}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [showFillModal, setShowFillModal] = useState(false);
  const [assetFilter, setAssetFilter] = useState("全部");
  const [confirmingFill, setConfirmingFill] = useState<string | null>(null);

  return (
    <div className="p-6 space-y-6 max-w-screen-2xl mx-auto">
      {/* Estimated banner */}
      <div
        className="flex items-center gap-3 px-4 py-2.5 rounded-lg border text-sm"
        style={{ background: "#FFFBEB", borderColor: "#FCD34D", color: "#92400E" }}
      >
        <AlertCircle size={15} />
        <span>
          组合含未正式确认 NAV 的场外基金持仓，总资产标记为
          <strong className="font-mono ml-1">估算</strong>，不得用于精确操作决策。
        </span>
        <span className="ml-auto text-xs font-mono" style={{ color: "#B45309" }}>
          as_of 2026-08-23 14:30
        </span>
      </div>

      {/* Metric cards */}
      <div className="grid grid-cols-2 xl:grid-cols-5 gap-4">
        <MetricCard
          label="总资产（估算）"
          value="¥1,921,840"
          sub="含场外基金估算 NAV"
          quality="ESTIMATED"
          asOf="2026-08-23 14:30"
        />
        <MetricCard
          label="可用现金"
          value="¥201,382"
          quality="FRESH"
          asOf="2026-08-23 12:00"
        />
        <MetricCard
          label="持仓市值"
          value="¥1,720,458"
          quality="ESTIMATED"
          asOf="2026-08-23 14:30"
        />
        <MetricCard
          label="当日盈亏"
          value="+¥12,384"
          sub="+0.65%"
          trend="up"
          quality="ESTIMATED"
          asOf="2026-08-23 14:30"
        />
        {/* 改进 #2：主数值显示累计盈亏金额，副标题显示 TWR，二者不混为一个指标 */}
        <MetricCard
          label="累计盈亏"
          value="+¥152,800"
          sub="+19.4% TWR · 超基准 +10.7%"
          trend="up"
          quality="FRESH"
          asOf="2026-08-23 15:00"
        />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* Equity curve */}
        <div
          className="xl:col-span-2 bg-white rounded-lg border p-5"
          style={{ borderColor: "#E2E8F0" }}
        >
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-sm font-semibold" style={{ color: "#0F172A" }}>
                组合净值走势
              </h2>
              <p className="text-xs mt-0.5" style={{ color: "#94A3B8" }}>
                基准：沪深300 · TWR · 2026-05-01 至今
              </p>
            </div>
            <div className="flex items-center gap-4 text-xs">
              <span className="flex items-center gap-1.5">
                <span className="w-3 h-0.5 rounded-full inline-block" style={{ background: "#0D9488" }} />
                <span style={{ color: "#64748B" }}>组合</span>
              </span>
              <span className="flex items-center gap-1.5">
                <span
                  className="w-3 h-0.5 rounded-full inline-block"
                  style={{ background: "#94A3B8", borderTop: "1px dashed #94A3B8" }}
                />
                <span style={{ color: "#64748B" }}>基准</span>
              </span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={equityData} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11, fill: "#94A3B8", fontFamily: "JetBrains Mono" }}
                tickLine={false}
                axisLine={false}
              />
              <YAxis
                tick={{ fontSize: 11, fill: "#94A3B8", fontFamily: "JetBrains Mono" }}
                tickLine={false}
                axisLine={false}
                domain={[97, 124]}
              />
              <Tooltip
                contentStyle={{
                  background: "#0F172A",
                  border: "none",
                  borderRadius: 6,
                  color: "#fff",
                  fontSize: 12,
                  fontFamily: "JetBrains Mono",
                }}
                itemStyle={{ color: "#fff" }}
                labelStyle={{ color: "#94A3B8", marginBottom: 4 }}
              />
              <Line
                type="monotone"
                dataKey="portfolio"
                stroke="#0D9488"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: "#0D9488" }}
                name="组合"
              />
              <Line
                type="monotone"
                dataKey="benchmark"
                stroke="#94A3B8"
                strokeWidth={1.5}
                strokeDasharray="4 3"
                dot={false}
                name="基准"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Asset allocation donut */}
        <div
          className="bg-white rounded-lg border p-5"
          style={{ borderColor: "#E2E8F0" }}
        >
          <h2 className="text-sm font-semibold mb-1" style={{ color: "#0F172A" }}>
            大类资产配置
          </h2>
          <p className="text-xs mb-3" style={{ color: "#94A3B8" }}>
            实际持仓比例（含现金）
          </p>
          <ResponsiveContainer width="100%" height={160}>
            <PieChart>
              <Pie
                data={allocationData}
                cx="50%"
                cy="50%"
                innerRadius={48}
                outerRadius={72}
                paddingAngle={3}
                dataKey="value"
              >
                {allocationData.map((entry, i) => (
                  <Cell key={i} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  background: "#0F172A",
                  border: "none",
                  borderRadius: 6,
                  color: "#fff",
                  fontSize: 12,
                  fontFamily: "JetBrains Mono",
                }}
                formatter={(v) => [`${v}%`, ""]}
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="space-y-2">
            {allocationData.map((d) => (
              <div key={d.name} className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <span
                    className="w-2 h-2 rounded-full shrink-0"
                    style={{ background: d.color }}
                  />
                  <span style={{ color: "#475569" }}>{d.name}</span>
                </div>
                <span className="font-mono font-medium" style={{ color: "#0F172A" }}>
                  {d.value}%
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Holdings table */}
      <div
        className="bg-white rounded-lg border"
        style={{ borderColor: "#E2E8F0" }}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: "#F1F5F9" }}>
          <div>
            <h2 className="text-sm font-semibold" style={{ color: "#0F172A" }}>
              持仓明细
            </h2>
            <p className="text-xs mt-0.5" style={{ color: "#94A3B8" }}>
              {holdings.length} 个标的 · 含估算
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex rounded overflow-hidden border text-xs" style={{ borderColor: "#E2E8F0" }}>
              {["全部", "A 股", "场内 ETF", "场外基金", "现金"].map((f) => (
                <button
                  key={f}
                  onClick={() => setAssetFilter(f)}
                  className="px-3 py-1.5 transition-colors"
                  style={{
                    background: assetFilter === f ? "#0D9488" : "#fff",
                    color: assetFilter === f ? "#fff" : "#475569",
                  }}
                >
                  {f}
                </button>
              ))}
            </div>
            <button
              className="flex items-center gap-1.5 px-3 py-1.5 rounded border text-xs transition-colors hover:bg-gray-50"
              style={{ borderColor: "#E2E8F0", color: "#475569" }}
            >
              <Filter size={12} />
              筛选
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ background: "#F8FAFC" }}>
                {["代码", "名称", "类型", "持仓份额", "成本价", "最新价", "市值", "浮动盈亏", "权重", "数据质量"].map(
                  (h) => (
                    <th
                      key={h}
                      className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider whitespace-nowrap"
                      style={{ color: "#64748B", borderBottom: "1px solid #F1F5F9" }}
                    >
                      <span className="flex items-center gap-1">
                        {h}
                        {["市值", "浮动盈亏", "权重"].includes(h) && (
                          <ArrowUpDown size={10} style={{ color: "#CBD5E1" }} />
                        )}
                      </span>
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody>
              {holdings
                .filter((h) => assetFilter === "全部" || h.type === assetFilter)
                .map((h, i) => (
                  <tr
                    key={h.code}
                    className="hover:bg-gray-50 transition-colors cursor-pointer"
                    style={{ borderBottom: "1px solid #F8FAFC" }}
                  >
                    <td className="px-4 py-3 font-mono text-xs font-medium" style={{ color: "#0D9488" }}>
                      {h.code}
                    </td>
                    <td className="px-4 py-3 font-medium text-xs" style={{ color: "#0F172A" }}>
                      {h.name}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className="text-xs px-2 py-0.5 rounded"
                        style={{ background: "#F1F5F9", color: "#475569" }}
                      >
                        {h.type}
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-right" style={{ color: "#0F172A" }}>
                      {h.shares}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-right" style={{ color: "#64748B" }}>
                      {h.cost}
                    </td>
                    <td
                      className="px-4 py-3 font-mono text-xs text-right font-medium"
                      style={{
                        color:
                          h.quality === "ESTIMATED" ? "#B45309" : "#0F172A",
                      }}
                    >
                      {h.price}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-right font-semibold" style={{ color: "#0F172A" }}>
                      ¥{h.value}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-right">
                      {h.pnlAmt !== "—" ? (
                        <div>
                          <div style={{ color: "#10B981" }}>{h.pnlAmt}</div>
                          <div style={{ color: "#10B981", fontSize: 10 }}>{h.pnlPct}</div>
                        </div>
                      ) : (
                        <span style={{ color: "#94A3B8" }}>—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-right" style={{ color: "#0F172A" }}>
                      {h.weight}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-col gap-1">
                        <QualityBadge q={h.quality} />
                        <span className="font-mono text-xs" style={{ color: "#CBD5E1", fontSize: 10 }}>
                          {h.asOf}
                        </span>
                      </div>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Action bar — 改进 #1：拆分出入金与录入成交两个独立按钮 */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          className="flex items-center gap-2 px-4 py-2 rounded border text-sm font-medium transition-colors hover:bg-teal-50"
          style={{ borderColor: "#0D9488", color: "#0D9488", background: "#fff" }}
        >
          <Plus size={15} />
          登记出入金
        </button>
        <button
          className="flex items-center gap-2 px-4 py-2 rounded text-sm font-medium transition-colors"
          style={{ background: "#0D9488", color: "#fff" }}
          onClick={() => setShowFillModal(true)}
        >
          <Plus size={15} />
          录入成交草稿
        </button>
        <button
          className="flex items-center gap-2 px-4 py-2 rounded border text-sm font-medium transition-colors hover:bg-gray-50"
          style={{ borderColor: "#E2E8F0", color: "#475569" }}
        >
          <RefreshCw size={14} />
          刷新行情
        </button>
        {pendingFills.length > 0 && (
          <button
            className="flex items-center gap-2 px-4 py-2 rounded border text-sm font-medium transition-colors"
            style={{ borderColor: "#FCD34D", background: "#FFFBEB", color: "#92400E" }}
            onClick={() => setShowFillModal(true)}
          >
            <Clock size={14} />
            {pendingFills.length} 笔成交草稿待确认
          </button>
        )}
      </div>

      {/* Fill confirmation modal — 改进 #3：结算预览 + 现金变动 + 批量操作 */}
      {showFillModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0"
            style={{ background: "rgba(15,23,42,0.4)" }}
            onClick={() => setShowFillModal(false)}
          />
          <div
            className="relative bg-white rounded-xl shadow-2xl w-full max-w-2xl mx-4"
            style={{ border: "1px solid #E2E8F0" }}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b" style={{ borderColor: "#F1F5F9" }}>
              <div>
                <h3 className="font-semibold" style={{ color: "#0F172A" }}>
                  成交草稿校对与确认
                </h3>
                <p className="text-xs mt-0.5" style={{ color: "#94A3B8" }}>
                  当前可用现金：<strong className="font-mono" style={{ color: "#0F172A" }}>¥201,382.00</strong>
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  className="text-xs px-3 py-1.5 rounded border font-medium transition-colors hover:bg-red-50"
                  style={{ borderColor: "#FCA5A5", color: "#DC2626" }}
                  onClick={() => setShowFillModal(false)}
                >
                  全部作废
                </button>
                <button
                  className="text-xs px-3 py-1.5 rounded font-medium transition-colors"
                  style={{ background: "#0D9488", color: "#fff" }}
                  onClick={() => {
                    setConfirmingFill("ALL");
                    setTimeout(() => setShowFillModal(false), 800);
                  }}
                >
                  一键确认全部
                </button>
                <button
                  onClick={() => setShowFillModal(false)}
                  className="text-xs px-3 py-1.5 rounded hover:bg-gray-100"
                  style={{ color: "#64748B" }}
                >
                  关闭
                </button>
              </div>
            </div>
            <div className="px-6 py-4 space-y-3">
              {pendingFills.map((fill) => {
                const qty = Number(fill.shares.replace(",", ""));
                const price = Number(fill.price);
                const fee = Number(fill.fee);
                const gross = qty * price;
                const total = gross + fee;
                const cashAfter = fill.direction === "BUY"
                  ? 201382 - total
                  : 201382 + gross - fee;

                return (
                  <div
                    key={fill.id}
                    className="rounded-lg border p-4 space-y-3"
                    style={{ borderColor: "#E2E8F0", background: "#F8FAFC" }}
                  >
                    <div className="flex items-start justify-between">
                      <div className="space-y-2">
                        <div className="flex items-center gap-2">
                          <span
                            className="text-xs px-2 py-0.5 rounded font-semibold"
                            style={{
                              background: fill.direction === "BUY" ? "#DCFCE7" : "#FEE2E2",
                              color: fill.direction === "BUY" ? "#166534" : "#991B1B",
                            }}
                          >
                            {fill.direction === "BUY" ? "买入" : "卖出"}
                          </span>
                          <span className="font-mono font-medium text-sm" style={{ color: "#0F172A" }}>
                            {fill.code} {fill.name}
                          </span>
                          <span className="text-xs" style={{ color: "#94A3B8" }}>
                            来自：{fill.source}
                          </span>
                        </div>
                        <div className="flex gap-6 text-xs font-mono" style={{ color: "#475569" }}>
                          <span>数量 <strong style={{ color: "#0F172A" }}>{fill.shares} 股</strong></span>
                          <span>价格 <strong style={{ color: "#0F172A" }}>¥{fill.price}</strong></span>
                          <span>手续费 <strong style={{ color: "#0F172A" }}>¥{fill.fee}</strong></span>
                          <span>时间 <strong style={{ color: "#0F172A" }}>{fill.time}</strong></span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0 ml-4">
                        {confirmingFill === fill.id || confirmingFill === "ALL" ? (
                          <span className="text-xs font-mono flex items-center gap-1" style={{ color: "#0D9488" }}>
                            <CheckCircle2 size={14} />
                            已确认
                          </span>
                        ) : (
                          <button
                            className="px-4 py-1.5 rounded text-xs font-semibold transition-colors"
                            style={{ background: "#0D9488", color: "#fff" }}
                            onClick={() => setConfirmingFill(fill.id)}
                          >
                            确认入账
                          </button>
                        )}
                      </div>
                    </div>
                    {/* 结算预览行 */}
                    <div
                      className="rounded border p-2.5 text-xs font-mono space-y-1"
                      style={{ background: "#fff", borderColor: "#E2E8F0" }}
                    >
                      <div style={{ color: "#475569" }}>
                        预计结算金额：
                        <span style={{ color: "#0F172A" }}>
                          ¥{gross.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}
                          {" + 手续费 ¥"}{fee.toFixed(2)}
                          {" = "}
                          <strong>¥{total.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}</strong>
                        </span>
                      </div>
                      <div style={{ color: "#475569" }}>
                        确认后可用现金预计变动：
                        <span style={{ color: "#0F172A" }}>
                          ¥201,382.00 →{" "}
                          <strong style={{ color: fill.direction === "BUY" ? "#EF4444" : "#10B981" }}>
                            ¥{cashAfter.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}
                          </strong>
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
              <p className="text-xs" style={{ color: "#94A3B8" }}>
                确认入账后记录不可撤销。错账请使用冲正功能。
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

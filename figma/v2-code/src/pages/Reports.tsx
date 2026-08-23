import { useState } from "react";
import {
  Sun,
  Activity,
  Clock,
  Moon,
  ChevronRight,
  X,
  ExternalLink,
  AlertTriangle,
  CheckCircle,
  MinusCircle,
  Link2,
} from "lucide-react";

const sessions = [
  { id: "PRE_MARKET", label: "08:30 盘前", icon: Sun, color: "#F59E0B" },
  { id: "INTRADAY", label: "盘中触点", icon: Activity, color: "#0EA5E9" },
  { id: "FUND_NAV", label: "14:30 基金估值", icon: Clock, color: "#8B5CF6" },
  { id: "POST_MARKET", label: "15:30 盘后复盘", icon: Moon, color: "#0D9488" },
];

const reports = [
  {
    id: "R20260823-4",
    session: "POST_MARKET",
    date: "2026-08-23",
    title: "15:30 盘后复盘 · 2026-08-23",
    summary: "今日沪深两市放量收涨，新能源板块领涨。宁德时代突破关键阻力位，知行合一复盘显示用户执行率 87%。",
    items: 6,
    decisions: 3,
    fills: 2,
    quality: "FRESH",
    publishedAt: "2026-08-23 16:02:18",
  },
  {
    id: "R20260823-3",
    session: "FUND_NAV",
    date: "2026-08-23",
    title: "14:30 场外基金估值 · 2026-08-23",
    summary: "易方达蓝筹精选估值 +1.20% ± 0.3%（持仓覆盖率 82%）；中欧医疗健康估值 +0.98% ± 0.4%。",
    items: 4,
    decisions: 0,
    fills: 0,
    quality: "ESTIMATED",
    publishedAt: "2026-08-23 14:35:42",
  },
  {
    id: "R20260823-2",
    session: "INTRADAY",
    date: "2026-08-23",
    title: "盘中触点 · 600519 突破 1700",
    summary: "贵州茅台于 10:24 突破 1700 元阻力位，触发持仓加仓触点。本次触点有效窗口 15 分钟，非订单确认。",
    items: 2,
    decisions: 1,
    fills: 0,
    quality: "FRESH",
    publishedAt: "2026-08-23 10:26:05",
  },
  {
    id: "R20260823-1",
    session: "PRE_MARKET",
    date: "2026-08-23",
    title: "08:30 盘前推演 · 2026-08-23",
    summary: "隔夜外盘美股纳指收涨 1.2%，A50 期指小幅高开。关注新能源 ETF 开盘方向，设置 15:55 止损出局条件。",
    items: 8,
    decisions: 4,
    fills: 0,
    quality: "FRESH",
    publishedAt: "2026-08-23 08:32:17",
  },
  {
    id: "R20260822-4",
    session: "POST_MARKET",
    date: "2026-08-22",
    title: "15:30 盘后复盘 · 2026-08-22",
    summary: "市场整体震荡，医药板块受政策影响走强。用户对建议采取 DEFERRED 动作 2 次，成交草稿确认 1 笔。",
    items: 5,
    decisions: 3,
    fills: 1,
    quality: "FRESH",
    publishedAt: "2026-08-22 16:01:54",
  },
];

const evidenceItems = [
  {
    id: "E001",
    claimType: "事实",
    content: "贵州茅台（600519）10:24 成交价 ¥1,712.50，较昨收 +1.08%",
    source: "行情数据提供商 · Level 2",
    asOf: "2026-08-23 10:24:03",
    publishedAt: "2026-08-23 10:24:05",
    quality: "FRESH",
    hash: "sha256:a3f4…e9c1",
  },
  {
    id: "E002",
    claimType: "解释",
    content: "1700 元为近 3 个月技术阻力位，突破可能引发筹码换手加速",
    source: "模型推断 · claude-opus-5 · skill:technical-analysis@v1.2",
    asOf: "2026-08-23 10:25:00",
    publishedAt: "2026-08-23 10:26:05",
    quality: "FRESH",
    hash: "sha256:b7d2…0a4f",
  },
  {
    id: "E003",
    claimType: "推断",
    content: "若维持在 1700 以上收盘，下一阻力位约 1750，上方空间约 2.2%",
    source: "模型推断 · claude-opus-5 · 无直接行情证据",
    asOf: "2026-08-23 10:25:00",
    publishedAt: "2026-08-23 10:26:05",
    quality: "ESTIMATED",
    hash: "sha256:c1e8…3b5d",
  },
];

function SessionTag({ sessionId }: { sessionId: string }) {
  const s = sessions.find((s) => s.id === sessionId)!;
  const Icon = s.icon;
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full font-medium"
      style={{ background: `${s.color}18`, color: s.color }}
    >
      <Icon size={11} />
      {s.label}
    </span>
  );
}

function QualityBadge({ q }: { q: string }) {
  const map: Record<string, string> = {
    FRESH: "badge-fresh",
    ESTIMATED: "badge-estimated",
  };
  const label: Record<string, string> = {
    FRESH: "实时",
    ESTIMATED: "估算",
  };
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded font-mono ${map[q] || "badge-unavailable"}`}>
      {label[q] || q}
    </span>
  );
}

export default function Reports() {
  const [selectedReport, setSelectedReport] = useState(reports[0]);
  const [sessionFilter, setSessionFilter] = useState<string | null>(null);
  const [showEvidence, setShowEvidence] = useState(false);
  const [highlightedEvidence, setHighlightedEvidence] = useState<string | null>(null);

  const openEvidenceAt = (id: string) => {
    setShowEvidence(true);
    setHighlightedEvidence(id);
    setTimeout(() => {
      document.getElementById(`ev-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 80);
  };

  const filtered = sessionFilter
    ? reports.filter((r) => r.session === sessionFilter)
    : reports;

  return (
    <div className="flex h-full" style={{ minHeight: 0 }}>
      {/* Left: list */}
      <div
        className="w-80 shrink-0 border-r flex flex-col"
        style={{ borderColor: "#E2E8F0", background: "#F8FAFC" }}
      >
        {/* Filter */}
        <div className="p-4 border-b space-y-3" style={{ borderColor: "#E2E8F0" }}>
          <h2 className="font-semibold text-sm" style={{ color: "#0F172A" }}>
            研报历史
          </h2>
          <div className="flex flex-wrap gap-1.5">
            <button
              onClick={() => setSessionFilter(null)}
              className="text-xs px-2.5 py-1 rounded-full transition-colors"
              style={{
                background: !sessionFilter ? "#0F172A" : "#E2E8F0",
                color: !sessionFilter ? "#fff" : "#475569",
              }}
            >
              全部
            </button>
            {sessions.map((s) => (
              <button
                key={s.id}
                onClick={() => setSessionFilter(s.id === sessionFilter ? null : s.id)}
                className="text-xs px-2.5 py-1 rounded-full transition-colors"
                style={{
                  background: sessionFilter === s.id ? s.color : "#E2E8F0",
                  color: sessionFilter === s.id ? "#fff" : "#475569",
                }}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {filtered.map((r) => (
            <button
              key={r.id}
              onClick={() => setSelectedReport(r)}
              className="w-full text-left px-4 py-3.5 border-b transition-colors"
              style={{
                borderColor: "#E2E8F0",
                background: selectedReport?.id === r.id ? "#fff" : "transparent",
                borderLeft: selectedReport?.id === r.id ? "3px solid #0D9488" : "3px solid transparent",
              }}
            >
              <div className="flex items-center justify-between mb-1.5">
                <SessionTag sessionId={r.session} />
                <span className="text-xs font-mono" style={{ color: "#94A3B8" }}>
                  {r.date}
                </span>
              </div>
              <p className="text-xs font-medium mb-1" style={{ color: "#0F172A" }}>
                {r.title}
              </p>
              <p className="text-xs line-clamp-2" style={{ color: "#64748B" }}>
                {r.summary}
              </p>
              <div className="flex items-center gap-3 mt-2 text-xs font-mono" style={{ color: "#94A3B8" }}>
                <span>{r.items} 条目</span>
                <span>{r.decisions} 决策</span>
                {r.fills > 0 && <span>{r.fills} 成交</span>}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Right: report viewer */}
      <div className="flex-1 overflow-y-auto">
        {selectedReport ? (
          <div className="p-6 space-y-6">
            {/* Header */}
            <div className="flex items-start justify-between">
              <div className="space-y-2">
                <SessionTag sessionId={selectedReport.session} />
                <h1 className="text-xl font-semibold" style={{ color: "#0F172A" }}>
                  {selectedReport.title}
                </h1>
                <div className="flex items-center gap-4 text-xs font-mono" style={{ color: "#64748B" }}>
                  <span>发布时间 {selectedReport.publishedAt}</span>
                  <QualityBadge q={selectedReport.quality} />
                </div>
              </div>
              <button
                onClick={() => setShowEvidence(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded border text-xs transition-colors hover:bg-gray-50"
                style={{ borderColor: "#E2E8F0", color: "#475569" }}
              >
                <ExternalLink size={12} />
                证据链
              </button>
            </div>

            {/* Summary */}
            <div
              className="rounded-lg border p-4 text-sm"
              style={{ borderColor: "#E2E8F0", background: "#F8FAFC", color: "#475569" }}
            >
              {selectedReport.summary}
            </div>

            {/* Session-specific content */}
            {selectedReport.session === "PRE_MARKET" && (
              <div className="space-y-4">
                <h2 className="text-sm font-semibold uppercase tracking-wider" style={{ color: "#64748B" }}>
                  盘前推演要点
                </h2>
                {/* 改进 #5：每张卡片悬停显示锚点链接，点击直接定位到对应证据 */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {[
                    { label: "隔夜外盘", value: "纳指 +1.2% · 标普 +0.8% · 恒指期货 +0.4%", type: "事实", evidenceId: "E001" },
                    { label: "A50 期指", value: "小幅高开 +0.3%，预计 A 股开盘情绪偏正面", type: "解释", evidenceId: "E002" },
                    { label: "支撑位", value: "300 ETF 3.720 · 茅台 1650", type: "事实", evidenceId: "E001" },
                    { label: "阻力位", value: "300 ETF 3.800 · 茅台 1730", type: "事实", evidenceId: "E001" },
                    { label: "If-Then 预案", value: "若茅台开盘跌破 1650，减仓 10%", type: "推断", evidenceId: "E003" },
                    { label: "出局条件", value: "300 ETF 收盘跌破 3.700 触发止损", type: "推断", evidenceId: "E003" },
                  ].map((item) => (
                    <div
                      key={item.label}
                      className="group rounded-lg border p-3 relative"
                      style={{ borderColor: "#E2E8F0", background: "#fff" }}
                    >
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-xs font-medium" style={{ color: "#0F172A" }}>
                          {item.label}
                        </span>
                        <div className="flex items-center gap-1.5">
                          {/* anchor link icon — 改进 #5 */}
                          <button
                            className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-gray-100"
                            title={`查看证据 #${item.evidenceId}`}
                            onClick={() => openEvidenceAt(item.evidenceId)}
                          >
                            <Link2 size={11} style={{ color: "#0D9488" }} />
                          </button>
                          <span
                            className="text-xs px-1.5 py-0.5 rounded"
                            style={{
                              background:
                                item.type === "事实"
                                  ? "#DCFCE7"
                                  : item.type === "解释"
                                  ? "#DBEAFE"
                                  : "#FEF9C3",
                              color:
                                item.type === "事实"
                                  ? "#166534"
                                  : item.type === "解释"
                                  ? "#1E40AF"
                                  : "#854D0E",
                            }}
                          >
                            {item.type}
                          </span>
                        </div>
                      </div>
                      <p className="text-xs" style={{ color: "#475569" }}>
                        {item.value}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {selectedReport.session === "FUND_NAV" && (
              <div className="space-y-4">
                <h2 className="text-sm font-semibold uppercase tracking-wider" style={{ color: "#64748B" }}>
                  场外基金盘中估值（非基金公司实时净值）
                </h2>
                {/* 改进 #4：增加持仓披露基准日，避免误以为是基金经理当天实时持仓 */}
                {[
                  {
                    code: "007540",
                    name: "易方达蓝筹精选",
                    estimate: "+1.20% ± 0.3%",
                    coverage: "82%",
                    disclosureDate: "2026-06-30 中报",
                    cutoff: "15:00（申购）/ 15:00（赎回）",
                    disclaimer: "估值区间基于持仓覆盖率 82% 推算，非官方 NAV，不得作为交易依据",
                  },
                  {
                    code: "001938",
                    name: "中欧医疗健康",
                    estimate: "+0.98% ± 0.4%",
                    coverage: "71%",
                    disclosureDate: "2026-06-30 中报",
                    cutoff: "15:00（申购）/ 15:00（赎回）",
                    disclaimer: "持仓覆盖率 71%，误差范围较大，建议参考官方 T+1 NAV 决策",
                  },
                ].map((fund) => (
                  <div
                    key={fund.code}
                    className="rounded-lg border p-4"
                    style={{ borderColor: "#E2E8F0" }}
                  >
                    <div className="flex items-start justify-between mb-3">
                      <div>
                        <span className="font-mono text-sm font-semibold" style={{ color: "#0D9488" }}>
                          {fund.code}
                        </span>
                        <span className="text-sm font-medium ml-2" style={{ color: "#0F172A" }}>
                          {fund.name}
                        </span>
                      </div>
                      <span
                        className="font-mono text-base font-semibold"
                        style={{ color: "#B45309" }}
                      >
                        {fund.estimate}
                      </span>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-xs mb-3">
                      <span style={{ color: "#64748B" }}>
                        持仓覆盖率 <strong style={{ color: "#0F172A" }}>{fund.coverage}</strong>
                      </span>
                      <span style={{ color: "#64748B" }}>
                        披露基准日{" "}
                        <strong style={{ color: "#475569" }} title="覆盖率基于此报告期持仓测算，非基金经理实时仓位">
                          {fund.disclosureDate}
                        </strong>
                      </span>
                      <span style={{ color: "#64748B" }}>
                        申赎截止 <strong style={{ color: "#0F172A" }}>{fund.cutoff}</strong>
                      </span>
                    </div>
                    <div
                      className="flex items-start gap-2 text-xs p-2.5 rounded"
                      style={{ background: "#FFFBEB", color: "#92400E" }}
                    >
                      <AlertTriangle size={12} className="shrink-0 mt-0.5" />
                      {fund.disclaimer}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {selectedReport.session === "POST_MARKET" && (
              <div className="space-y-4">
                <h2 className="text-sm font-semibold uppercase tracking-wider" style={{ color: "#64748B" }}>
                  知行合一复盘
                </h2>
                <div
                  className="rounded-lg border overflow-hidden"
                  style={{ borderColor: "#E2E8F0" }}
                >
                  {/* 改进 #6：增加"早盘计划点位"列，让执行偏差一目了然 */}
                  <table className="w-full text-xs">
                    <thead style={{ background: "#F8FAFC" }}>
                      <tr>
                        {["标的", "早盘计划点位", "建议方向", "用户决策", "执行情况", "滑点"].map((h) => (
                          <th
                            key={h}
                            className="text-left px-4 py-2.5 font-medium uppercase tracking-wider whitespace-nowrap"
                            style={{ color: "#64748B", borderBottom: "1px solid #E2E8F0" }}
                          >
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {[
                        {
                          asset: "600519 贵州茅台",
                          plan: "突破 1700 加仓 5%",
                          direction: "持有",
                          decision: "ACCEPTED",
                          execution: "无操作（持有）",
                          slippage: "—",
                        },
                        {
                          asset: "300750 宁德时代",
                          plan: "企稳 210 后加仓",
                          direction: "加仓 5%",
                          decision: "IGNORED",
                          execution: "未执行",
                          slippage: "—",
                        },
                        {
                          asset: "159919 300ETF",
                          plan: "跌破 3.70 止损",
                          direction: "持有",
                          decision: "ACCEPTED",
                          execution: "无操作",
                          slippage: "—",
                        },
                      ].map((row, i) => (
                        <tr
                          key={i}
                          style={{ borderBottom: "1px solid #F8FAFC" }}
                          className="hover:bg-gray-50"
                        >
                          <td className="px-4 py-3 font-mono font-medium" style={{ color: "#0F172A" }}>
                            {row.asset}
                          </td>
                          <td className="px-4 py-3 font-mono text-xs" style={{ color: "#64748B" }}>
                            {row.plan}
                          </td>
                          <td className="px-4 py-3" style={{ color: "#475569" }}>
                            {row.direction}
                          </td>
                          <td className="px-4 py-3">
                            <span
                              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium"
                              style={{
                                background:
                                  row.decision === "ACCEPTED"
                                    ? "#DCFCE7"
                                    : row.decision === "IGNORED"
                                    ? "#FEE2E2"
                                    : "#F1F5F9",
                                color:
                                  row.decision === "ACCEPTED"
                                    ? "#166534"
                                    : row.decision === "IGNORED"
                                    ? "#991B1B"
                                    : "#475569",
                              }}
                            >
                              {row.decision === "ACCEPTED" ? (
                                <CheckCircle size={10} />
                              ) : (
                                <MinusCircle size={10} />
                              )}
                              {row.decision}
                            </span>
                          </td>
                          <td className="px-4 py-3" style={{ color: "#475569" }}>
                            {row.execution}
                          </td>
                          <td className="px-4 py-3 font-mono" style={{ color: "#94A3B8" }}>
                            {row.slippage}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {selectedReport.session === "INTRADAY" && (
              <div
                className="rounded-lg border p-4"
                style={{ borderColor: "#FCD34D", background: "#FFFBEB" }}
              >
                <div className="flex items-center gap-2 mb-2">
                  <Activity size={14} style={{ color: "#F59E0B" }} />
                  <span className="text-sm font-semibold" style={{ color: "#92400E" }}>
                    盘中触点说明
                  </span>
                </div>
                <p className="text-xs" style={{ color: "#92400E" }}>
                  本触点为价格突破预设阈值的通知提醒，<strong>不是买卖订单</strong>，不会自动执行任何交易。
                  有效窗口 15 分钟内请自行判断是否操作并在企业微信或 Web 端确认成交。
                </p>
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center justify-center h-full" style={{ color: "#94A3B8" }}>
            请选择一份研报查看详情
          </div>
        )}
      </div>

      {/* Evidence drawer */}
      {showEvidence && (
        <div
          className="w-96 shrink-0 border-l flex flex-col"
          style={{ borderColor: "#E2E8F0", background: "#fff" }}
        >
          <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: "#E2E8F0" }}>
            <h3 className="text-sm font-semibold" style={{ color: "#0F172A" }}>
              证据链溯源
            </h3>
            <button onClick={() => setShowEvidence(false)}>
              <X size={16} style={{ color: "#64748B" }} />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-5 space-y-3">
            {evidenceItems.map((e) => (
              <div
                id={`ev-${e.id}`}
                key={e.id}
                className="rounded-lg border p-3 space-y-2 transition-all duration-300"
                style={{
                  borderColor: highlightedEvidence === e.id ? "#0D9488" : "#E2E8F0",
                  background: highlightedEvidence === e.id ? "#F0FDFA" : "#fff",
                  boxShadow: highlightedEvidence === e.id ? "0 0 0 2px #0D948833" : "none",
                }}
              >
                <div className="flex items-center gap-2">
                  <span
                    className="text-xs px-2 py-0.5 rounded font-medium"
                    style={{
                      background:
                        e.claimType === "事实"
                          ? "#DCFCE7"
                          : e.claimType === "解释"
                          ? "#DBEAFE"
                          : "#FEF9C3",
                      color:
                        e.claimType === "事实"
                          ? "#166534"
                          : e.claimType === "解释"
                          ? "#1E40AF"
                          : "#854D0E",
                    }}
                  >
                    {e.claimType}
                  </span>
                  <span className="font-mono text-xs" style={{ color: "#94A3B8" }}>
                    #{e.id}
                  </span>
                  {highlightedEvidence === e.id && (
                    <span className="text-xs font-mono ml-auto" style={{ color: "#0D9488" }}>
                      ← 当前锚点
                    </span>
                  )}
                </div>
                <p className="text-xs" style={{ color: "#0F172A" }}>
                  {e.content}
                </p>
                <div className="space-y-1 text-xs font-mono" style={{ color: "#64748B" }}>
                  <div>来源：{e.source}</div>
                  <div>as_of {e.asOf}</div>
                  <div>hash {e.hash}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

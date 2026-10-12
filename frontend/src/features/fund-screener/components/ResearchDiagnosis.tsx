import { useState, type ReactNode } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { RefreshCw } from "lucide-react";
import type { Analysis } from "../../../api/investment";
const labels: Record<string, string> = {
  NOT_COLLECTED: "数据待接入",
  UNVERIFIED: "来源待核验",
  STALE: "数据待更新",
  CONFLICT: "数据冲突",
  UNSUPPORTED: "不支持完整评分",
  INSUFFICIENT: "历史不足",
  STRONG: "趋势较强",
  MIXED: "信号不一致",
  WEAK: "趋势偏弱",
};
export default function ResearchDiagnosis({
  title,
  code,
  mode,
  path,
  data,
  loading,
  error,
  onRefresh,
  refreshing,
  canRefresh,
  message,
  watchButton,
}: {
  title: string;
  code?: string;
  mode: "index" | "fund";
  path: string;
  data?: Analysis;
  loading: boolean;
  error: boolean;
  onRefresh: () => void;
  refreshing: boolean;
  canRefresh: boolean;
  message: string;
  watchButton: ReactNode;
}) {
  const [tab, setTab] = useState("decision");
  return (
    <aside className="op-panel op-diagnosis" aria-label="研究诊断">
      <header>
        <div className="op-diagnosis-top">
          <span className="op-mode">
            {mode === "fund" ? "基金研究" : "指数研究"}
          </span>
          <small>{path}</small>
          {watchButton}
        </div>
        <h2>{title}</h2>
        <p className="op-object-code">
          {code ?? "—"} · {mode === "fund" ? "基金" : "指数"}
        </p>
      </header>
      <div role="tablist" aria-label="诊断内容" className="op-tabs">
        {[
          ["decision", "综合判断"],
          ["trend", "技术趋势"],
          ["quality", "产品质量"],
        ].map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            aria-controls={`op-tab-${id}`}
            id={`op-tab-button-${id}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        className="op-diagnosis-body"
        role="tabpanel"
        id={`op-tab-${tab}`}
        aria-labelledby={`op-tab-button-${tab}`}
      >
        {error && <p role="alert">诊断读取失败，请重试。</p>}
        {loading && <p role="status">诊断加载中…</p>}
        {tab === "decision" && (
          <>
            <div className="op-summary">
              <div>
                <strong>核心结论</strong>
                <p>
                  {data?.score != null
                    ? `技术状态：${labels[data.state] ?? data.state}。技术分数仅描述趋势，长期配置价值和组合风险仍需独立研究。`
                    : "关键研究数据尚未齐备，暂不形成配置或买卖结论。"}
                </p>
              </div>
              <span className="op-badge">
                {data?.score != null ? `${data.score} / 100` : "尚未评估"}
              </span>
            </div>
            <dl className="op-conclusions">
              {[
                ["标的价值", "长期配置研究结论"],
                ["产品质量", "成本、跟踪与执行效率"],
                ["组合适配", "仓位、相关性与风险预算"],
              ].map(([a, b]) => (
                <div key={a}>
                  <dt>{a}</dt>
                  <dd>{b}</dd>
                  <span>尚未评估</span>
                </div>
              ))}
            </dl>
            <div className="op-next">
              <strong>下一步观察</strong>
              <p>
                {data?.reasons.length
                  ? data.reasons
                      .filter((r) => !r.passed)
                      .map((r) => r.reason)
                      .join("；") || "持续核验趋势与产品质量，关注条件变化。"
                  : "先确认研究数据与来源，再观察产品成本、跟踪质量与组合风险。"}
              </p>
            </div>
          </>
        )}
        {tab === "trend" && (
          <>
            <p>
              {labels[data?.state ?? "NOT_COLLECTED"] ?? "尚未评估"} ·{" "}
              {data?.seriesKind === "ACCUMULATED_NAV"
                ? "累计净值，不代表分红再投资总回报"
                : "价格趋势"}
            </p>
            {data?.points.length ? (
              <div className="op-trend-chart">
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={data.points.slice(-260)}>
                    <XAxis dataKey="date" minTickGap={45} />
                    <YAxis domain={["auto", "auto"]} width={55} />
                    <Tooltip />
                    <Line
                      dataKey="close"
                      name="价格 / 净值"
                      dot={false}
                      stroke="#0d9488"
                      strokeWidth={2}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="op-empty">暂无可靠历史序列，无法绘制趋势图。</p>
            )}
            <div className="op-indicators">
              {Object.entries(data?.indicators ?? {}).map(([k, v]) => (
                <div key={k}>
                  <span>{k}</span>
                  <strong>{v == null ? "不适用" : Number(v).toFixed(4)}</strong>
                </div>
              ))}
            </div>
            {data?.reasons.map((r) => (
              <p className="op-reason" key={r.key}>
                {r.passed ? "✓" : "○"} {r.reason}
                <span>
                  {r.points} / {r.weight}
                </span>
              </p>
            ))}
          </>
        )}
        {tab === "quality" && (
          <>
            <h3>{mode === "fund" ? "基金质量与执行成本" : "指数研究口径"}</h3>
            <p>
              管理费、托管费、跟踪误差、规模、流动性与折溢价尚未完成来源核验，不生成产品质量排名。
            </p>
            <p>
              指数风险暴露与基金质量分别评价，同指数基金不能只按技术分数选优。
            </p>
          </>
        )}
        <div className="op-provenance">
          <strong>数据依据</strong>
          <p>
            {data?.source ?? "来源待接入"} · 截止：{data?.asOf ?? "未知"}
          </p>
          {data?.algorithmVersion && (
            <small>
              方法：{data.algorithmVersion} · 输入：
              {data.inputHash?.slice(0, 12)}
            </small>
          )}
        </div>
        <button
          className="op-refresh"
          disabled={refreshing || !canRefresh}
          onClick={onRefresh}
        >
          <RefreshCw size={14} />
          {refreshing ? "后台更新中…" : "更新研究数据"}
        </button>
        {message && <p role="status">{message}</p>}
        <p className="op-disclaimer">
          技术分数不是胜率。研究结果不自动执行交易。
        </p>
      </div>
    </aside>
  );
}

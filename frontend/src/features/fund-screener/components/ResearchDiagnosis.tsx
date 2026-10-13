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
  READY: "数据齐备",
};
const seriesLabels: Record<string, string> = {
  UNIT_NAV: "单位净值，分红日可能降低，不能直接视为总收益",
  PROVIDER_RETURN_INDEX: "供应商分红再投收益指数，经登记核验后用于历史研究",
  ETF_HFQ: "ETF 后复权价格，采用供应商分红与拆分调整口径",
  INDEX_PRICE: "指数价格，不包含分红再投总收益",
  ACCUMULATED_NAV: "累计净值，不代表分红再投资总回报",
};
const qualificationLabels: Record<string, string> = {
  RESEARCH_CANDIDATE: "满足研究门槛",
  NOT_CANDIDATE: "未达研究门槛",
  INCOMPLETE: "证据不足",
};
function percent(value: number | null | undefined, fraction = true) {
  return value == null || !Number.isFinite(value)
    ? "—"
    : `${(value * (fraction ? 100 : 1)).toFixed(2)}%`;
}
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
  const [window, setWindow] = useState("1y");
  const allPoints = data?.points ?? [];
  const history =
    window === "all"
      ? allPoints
      : allPoints.slice(window === "3y" ? -756 : -253);
  const performance = data?.performance;
  const valueEvidence = data?.valueEvidence;
  const costs = data?.costs ?? valueEvidence?.evidence.costs;
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
            <section aria-label="历史收益与风险">
              <h3>历史收益与风险</h3>
              <p>
                {labels[performance?.state ?? "NOT_COLLECTED"] ?? "尚未评估"} ·
                独立历史证据
              </p>
              <dl className="op-conclusions">
                {(
                  [
                    ["63 观察日收益", performance?.return63],
                    ["252 观察日收益", performance?.return252],
                    ["样本最大回撤", performance?.maxDrawdown],
                    ["20 观察日年化波动率", performance?.volatility20],
                  ] as const
                ).map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{percent(value)}</dd>
                  </div>
                ))}
              </dl>
              <p>
                实际区间：{performance?.historyStart ?? "未知"} 至{" "}
                {performance?.historyEnd ?? "未知"} ·{" "}
                {performance?.sampleCount ?? 0} 个实际观察值
              </p>
              {performance?.reasons.map((reason) => (
                <p key={reason}>{reason}</p>
              ))}
            </section>
            <section aria-label="研究资格证据">
              <h3>
                研究资格：
                {qualificationLabels[valueEvidence?.state ?? "INCOMPLETE"]}
              </h3>
              <p>
                {valueEvidence?.eligible
                  ? "仅作为研究候选，尚未证实长期配置价值。"
                  : "当前证据尚未满足研究资格，需继续核对缺失或未达标项目。"}
              </p>
              {valueEvidence?.reasons.map((reason) => (
                <p key={reason}>{reason}</p>
              ))}
              <p>
                PE 分位：
                {percent(valueEvidence?.evidence.pe?.percentile, false)}
              </p>
              <p>
                PB 分位：
                {percent(valueEvidence?.evidence.pb?.percentile, false)}
              </p>
              {valueEvidence?.evidence.limitations?.map((reason) => (
                <p key={reason}>{reason}</p>
              ))}
            </section>
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
              {seriesLabels[data?.seriesKind ?? ""] ?? "历史序列口径待核实"}
            </p>
            <label>
              历史走势窗口
              <select
                aria-label="历史走势窗口"
                value={window}
                onChange={(event) => setWindow(event.target.value)}
              >
                <option value="1y">近 1 年（最多 253 个观察值）</option>
                <option value="3y">近 3 年（最多 756 个观察值）</option>
                <option value="all">全部实际历史</option>
              </select>
            </label>
            <p>
              展示 {history.length} 个实际观察值
              {history.length > 0 &&
                ` · ${history[0].date} 至 ${history.at(-1)!.date}`}
            </p>
            {data?.points.length ? (
              <div className="op-trend-chart">
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={history}>
                    <XAxis dataKey="date" minTickGap={45} />
                    <YAxis domain={["auto", "auto"]} width={55} />
                    <Tooltip />
                    <Line
                      dataKey="close"
                      name={
                        data.seriesKind === "PROVIDER_RETURN_INDEX"
                          ? "收益指数"
                          : data.seriesKind === "UNIT_NAV" ||
                              data.seriesKind === "ACCUMULATED_NAV"
                            ? "净值"
                            : "价格"
                      }
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
            <p>管理费：{percent(costs?.managementRate)}</p>
            <p>托管费：{percent(costs?.custodyRate)}</p>
            <p>销售服务费：{percent(costs?.salesServiceRate)}</p>
            <p>
              年运作费用合计：
              {percent(
                costs?.totalRate ?? valueEvidence?.evidence.costs?.totalRate,
              )}
            </p>
            <p>
              费用状态：{labels[costs?.state ?? "NOT_COLLECTED"] ?? "尚未评估"}
            </p>
            <p>
              {costs?.sourceUri ? (
                <a href={costs.sourceUri} target="_blank" rel="noreferrer">
                  费用披露来源
                </a>
              ) : (
                "费用来源尚未核验"
              )}{" "}
              · 获取：{costs?.retrievedAt ?? "未知"}
            </p>
            <p>
              跟踪误差、流动性、折溢价与组合适配尚未评估，不生成完整产品质量排名。
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

import { useState } from "react";
import { Globe2, ChevronDown } from "lucide-react";
import type { MacroContext } from "../../../api/investment";

type MacroItem = MacroContext["items"][number];

function HistoricalTrend({ item }: { item: MacroItem }) {
  const history = (item.history ?? []).filter(
    (p) => Number.isFinite(p.value) && Number.isFinite(Date.parse(p.asOf)),
  );
  if (!history.length) return <p>历史序列尚未采集，暂不绘制走势。</p>;
  if (history.length < 2) return <p>历史样本不足，暂不绘制走势。</p>;
  if (new Set(history.map((p) => p.unit ?? item.unit)).size > 1)
    return <p>历史单位发生变化，暂不绘制可比较走势。</p>;
  const low = Math.min(...history.map((p) => p.value));
  const high = Math.max(...history.map((p) => p.value));
  const first = Date.parse(history[0].asOf);
  const last = Date.parse(history.at(-1)!.asOf);
  const points = history.map((p) => ({
    ...p,
    x: 10 + ((Date.parse(p.asOf) - first) / (last - first || 1)) * 300,
    y: high === low ? 50 : 90 - ((p.value - low) / (high - low)) * 80,
    month: Number(p.asOf.slice(0, 4)) * 12 + Number(p.asOf.slice(5, 7)),
    sourceKey: p.sourceUri ?? p.source ?? item.sourceUri ?? item.source,
  }));
  return (
    <figure style={{ margin: "12px 0" }}>
      <svg
        role="img"
        aria-label={`${item.label} 历史走势`}
        viewBox="0 0 320 100"
        width="100%"
        height={100}
      >
        <title>{item.label} 实际历史样本，缺失月份不连接</title>
        {points.map((point, i) => (
          <g key={point.asOf}>
            {i > 0 &&
              point.month - points[i - 1].month === 1 &&
              point.sourceKey === points[i - 1].sourceKey && (
                <line
                  x1={points[i - 1].x}
                  y1={points[i - 1].y}
                  x2={point.x}
                  y2={point.y}
                  stroke="#0d9488"
                  strokeWidth={2}
                />
              )}
            <circle cx={point.x} cy={point.y} r={2.5} fill="#0d9488">
              <title>
                {point.asOf}：{point.value} {point.unit ?? item.unit ?? ""}
              </title>
            </circle>
          </g>
        ))}
      </svg>
      <figcaption>
        {history[0].asOf} 至 {history.at(-1)!.asOf}
        <br />
        取值范围：{low.toFixed(2)}–{high.toFixed(2)} {item.unit ?? ""}
      </figcaption>
    </figure>
  );
}

function periodChange(item: MacroItem) {
  if (item.periodChange == null || !Number.isFinite(item.periodChange))
    return "暂不可计算";
  const unit = item.unit === "%" ? "个百分点" : (item.unit ?? "");
  return `${item.periodChange > 0 ? "+" : ""}${item.periodChange.toFixed(2)} ${unit}`.trim();
}

export default function MacroContextStrip({
  data,
  error,
  months = 36,
  onMonthsChange,
}: {
  data?: MacroContext;
  error: boolean;
  months?: number;
  onMonthsChange?: (months: number) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="op-panel">
      <button
        className="op-macro-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span>
          <Globe2 size={19} />
          <strong>市场环境概览</strong>
          <span className="op-muted">增长 · 通胀 · 流动性 · 风险</span>
        </span>
        <span>
          <span className="op-badge">
            {error
              ? "读取失败"
              : data?.state === "READY"
                ? "事实已更新"
                : "数据待核验"}
          </span>
          <ChevronDown size={16} />
        </span>
      </button>
      {open && (
        <>
          <div className="op-filters">
            <label>
              宏观观察窗口
              <select
                aria-label="宏观观察窗口"
                value={months}
                onChange={(event) =>
                  onMonthsChange?.(Number(event.target.value))
                }
              >
                {[12, 36, 60].map((window) => (
                  <option key={window} value={window}>
                    {window} 个月
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="op-macro-grid">
            {data?.items.length ? (
              data.items.map((i) => (
                <article key={i.code}>
                  <strong>{i.label}</strong>
                  <h3>
                    {i.value == null
                      ? "—"
                      : `${i.value} ${i.unit ?? ""}`.trim()}
                  </h3>
                  <p>
                    已采集 {i.sampleCount ?? i.history?.length ?? 0} 个历史样本
                    · {data?.months ?? months} 个月窗口
                  </p>
                  <HistoricalTrend item={i} />
                  <p>连续月变化：{periodChange(i)}</p>
                  {i.trendState === "GAP" && (
                    <p>历史月份有缺口，仅展示实际样本。</p>
                  )}
                  {i.trendState === "INSUFFICIENT" && (
                    <p>历史样本不足，暂不计算变化。</p>
                  )}
                  <p>
                    观察期：{i.asOf ?? "未知"} · {i.state}
                  </p>
                  <p>
                    {i.publishedAt
                      ? `公布：${i.publishedAt}`
                      : "公布时间未核实，仅展示观察期事实。"}{" "}
                    · 来源：{i.source ?? "未知"}
                  </p>
                </article>
              ))
            ) : (
              <p>宏观数据尚未接入。方向探索和基金搜索仍可使用。</p>
            )}
          </div>
        </>
      )}
    </section>
  );
}

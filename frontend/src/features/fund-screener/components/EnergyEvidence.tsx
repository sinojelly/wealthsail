import { useState } from "react";
import type { DailyResearch } from "../../../api/investment";
import { sourceHref, timeLabel } from "../macro-display";
import { energyUnit } from "../energy-display";
import { useEvidenceChart } from "./useEvidenceChart";

type EnergyRow = NonNullable<DailyResearch["report"]>["energy"][number];
const finite = (value: number | null): value is number =>
  value != null && Number.isFinite(value);
const number = (value: number | null) =>
  finite(value) ? value.toFixed(3) : "—";

function TwoObservations({ row }: { row: EnergyRow }) {
  const [plotRef, chart] = useEvidenceChart();
  const [selected, setSelected] = useState<"previous" | "current" | null>(null);
  if (!finite(row.previousValue))
    return <p>前次观察缺少有效数值，暂不绘制两次观察。</p>;
  if (!finite(row.value))
    return <p>当前观察缺少有效数值，暂不绘制两次观察。</p>;
  if (
    !row.previousDate ||
    !row.asOf ||
    !Number.isFinite(Date.parse(row.previousDate)) ||
    !Number.isFinite(Date.parse(row.asOf))
  )
    return <p>观察日期无效，暂不绘制两次观察。</p>;
  if (Date.parse(row.previousDate) >= Date.parse(row.asOf))
    return <p>前次观察应早于当前观察，暂不绘制连线。</p>;
  if (!["USD/barrel", "USD/gallon"].includes(row.unit))
    return <p>能源单位未确认，暂不绘制可比较观察。</p>;
  if (!["DAILY", "WEEKLY"].includes(row.frequency))
    return <p>观察频率未确认，暂不绘制可比较观察。</p>;
  if (!["READY", "STALE"].includes(row.state))
    return <p>能源观察存在冲突或尚未核实，暂不绘制连线。</p>;
  const low = Math.min(row.previousValue, row.value),
    high = Math.max(row.previousValue, row.value),
    padding = Math.max((high - low) * 0.1, 0.1);
  const y = (value: number) =>
    chart.bottom -
    ((value - low + padding) / (high - low + 2 * padding)) *
      (chart.bottom - chart.top);
  const points = [
    {
      id: "previous" as const,
      date: row.previousDate,
      value: row.previousValue,
      x: chart.left,
    },
    {
      id: "current" as const,
      date: row.asOf,
      value: row.value,
      x: chart.right,
    },
  ];
  const active = points.find((point) => point.id === selected);
  return (
    <figure ref={plotRef} className="op-macro-chart op-evidence-plot">
      <svg
        role="img"
        aria-label={`${row.label} 两次实际观察`}
        viewBox={`0 0 ${chart.width} ${chart.height}`}
        width="100%"
        height={chart.height}
      >
        <title>
          {row.label} 前次与当前实际观察，连线不代表中间日期连续数据
        </title>
        <text x={4} y={15}>
          {energyUnit(row.unit)}
        </text>
        {[low - padding, (low + high) / 2, high + padding].map((tick) => (
          <g key={tick}>
            <text x={chart.left - 6} y={y(tick) + 4} textAnchor="end">
              {tick.toFixed(1)}
            </text>
            <path
              d={`M${chart.left} ${y(tick)} H${chart.right}`}
              stroke="#e2e8f0"
              fill="none"
            />
          </g>
        ))}
        <line
          x1={points[0].x}
          y1={y(points[0].value)}
          x2={points[1].x}
          y2={y(points[1].value)}
          stroke="#0d9488"
          strokeWidth={2}
          strokeDasharray="4 3"
        />
        {points.map((point) => (
          <circle
            key={point.id}
            role="button"
            tabIndex={0}
            aria-label={`查看${row.label} ${timeLabel(point.date)}`}
            cx={point.x}
            cy={y(point.value)}
            r={4}
            fill="#0d9488"
            onFocus={() => setSelected(point.id)}
            onMouseEnter={() => setSelected(point.id)}
            onClick={() => setSelected(point.id)}
            onKeyDown={(event) => {
              if (["Enter", " "].includes(event.key)) {
                event.preventDefault();
                setSelected(point.id);
              }
            }}
          />
        ))}
        <text x={chart.left} y={chart.height - 14}>
          {row.previousDate}
        </text>
        <text x={chart.right} y={chart.height - 14} textAnchor="end">
          {row.asOf}
        </text>
      </svg>
      <figcaption>
        仅展示前次与当前两次实际观察；虚线辅助比较，不代表完整历史或中间日期连续数据。
      </figcaption>
      <p role="status" aria-label={`${row.label} 观察明细`}>
        {active
          ? `${timeLabel(active.date)}：${number(active.value)} ${energyUnit(row.unit)}`
          : "悬停或键盘聚焦查看实际观察值。"}
      </p>
    </figure>
  );
}

export default function EnergyEvidence({ row }: { row: EnergyRow }) {
  const href = sourceHref(row.sourceUri);
  return (
    <article className="op-evidence-card">
      <strong>{row.label}</strong>
      <div className="op-evidence-layout">
        <TwoObservations row={row} />
        <div className="op-evidence-facts">
          <p className="op-series-value">
            {number(row.value)} {energyUnit(row.unit)}
          </p>
          <p>
            相邻变化：
            {finite(row.change)
              ? `${row.change > 0 ? "+" : ""}${number(row.change)} ${energyUnit(row.unit)}`
              : "暂不可计算"}
          </p>
          <p>
            前次观察：{timeLabel(row.previousDate)} ·{" "}
            {number(row.previousValue)} {energyUnit(row.unit)}
          </p>
          <p>
            当前观察：{timeLabel(row.asOf)} ·{" "}
            {row.frequency === "WEEKLY" ? "周度" : "日度"}
          </p>
          <p>
            {row.state === "READY"
              ? "当前事实可用"
              : row.state === "STALE"
                ? "旧期证据，需更新"
                : "来源或状态待核验"}
          </p>
          <p>能源价格观察不认证事件因果，也不能单独判断企业利润变化。</p>
        </div>
      </div>
      <details className="op-evidence-metadata">
        <summary>查看{row.label}数据依据</summary>
        <p>
          获取：{timeLabel(row.retrievedAt)}；观察日期不代表已核实的公布时刻。
        </p>
        <p>
          来源记录 {row.sampleCount}{" "}
          个观察样本；本接口仅返回前次与当前观察，未提供完整历史序列。
        </p>
        {href && (
          <a href={href} target="_blank" rel="noreferrer">
            查看{row.label}来源
          </a>
        )}
      </details>
    </article>
  );
}

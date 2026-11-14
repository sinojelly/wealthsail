import type { OpportunityContext } from "../../../api/investment";
import { cycleTrend, type CycleFact } from "../macro-cycle-overview";
import { sourceHref } from "../macro-display";

const economySeries = [
  { code: "PMI", label: "制造业 PMI", color: "#146556", axis: "primary" },
  { code: "CPI_YOY", label: "CPI 同比", color: "#376db7", axis: "secondary" },
  { code: "M1_YOY", label: "M1 同比", color: "#9b5d21", axis: "secondary" },
  { code: "PPI_YOY", label: "PPI 同比", color: "#a75178", axis: "secondary" },
] as const;
const ratesSeries = [
  {
    code: "US_T10_NOMINAL",
    label: "美国 10Y 名义",
    color: "#b3453c",
    axis: "primary",
  },
  {
    code: "US_T10_REAL",
    label: "美国 10Y 实际",
    color: "#8261a8",
    axis: "primary",
  },
  { code: "CN_T10", label: "中国 10Y", color: "#167466", axis: "primary" },
] as const;

const monthIndex = (date: string) =>
  Number(date.slice(0, 4)) * 12 + Number(date.slice(5, 7)) - 1;
const monthName = (index: number) =>
  `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;

function Trend({
  data,
  supplemental,
  cutoff,
  economy,
}: {
  data: OpportunityContext;
  supplemental?: OpportunityContext;
  cutoff?: string | null;
  economy: boolean;
}) {
  const lines = (economy ? economySeries : ratesSeries)
    .map((line) => ({
      ...line,
      points: cycleTrend(data, line.code, cutoff, supplemental),
    }))
    .filter((line) => line.points.length);
  const title = economy ? "中国制造业景气与价格趋势" : "中美国债收益率趋势";
  if (!lines.length)
    return <p className="op-cycle-missing">暂无本地可绘制的历史序列。</p>;
  const months = lines.flatMap((line) =>
    line.points.map((point) => monthIndex(point.date)),
  );
  const last = Math.max(...months);
  const first = Math.min(last - 1, Math.max(last - 35, Math.min(...months)));
  const x = (month: number) => 37 + ((month - first) / (last - first)) * 357;
  const scale = (samples: number[]) => {
    const min = Math.min(...samples);
    const max = Math.max(...samples);
    const pad = Math.max((max - min) * 0.09, 0.2);
    return (value: number) =>
      143 - ((value - min + pad) / (max - min + 2 * pad)) * 125;
  };
  const y = scale([
    ...(economy ? [50] : [0, 2.5, 4.5]),
    ...lines
      .filter((line) => line.axis === "primary")
      .flatMap((line) => line.points.map((point) => point.value)),
  ]);
  const secondaryY = scale([
    0,
    ...lines
      .filter((line) => line.axis === "secondary")
      .flatMap((line) => line.points.map((point) => point.value)),
  ]);
  const threshold = economy ? 50 : 4.5;
  const dateTicks = [...new Set([first, Math.floor((first + last) / 2), last])];
  return (
    <>
      <svg
        className="op-cycle-trend-plot"
        viewBox="0 0 435 165"
        role="img"
        aria-label={title}
      >
        <title>{title}，每月末可用观测，缺月和统计口径变化处留空</title>
        <rect
          x="37"
          y="18"
          width="357"
          height={Math.max(0, y(threshold) - 18)}
          fill={economy ? "#e7f4ed" : "#f9e8e6"}
        />
        {economy && (
          <rect
            x="37"
            y={y(50)}
            width="357"
            height={143 - y(50)}
            fill="#edf2f9"
          />
        )}
        <path d="M37 18V143H394" fill="none" stroke="#c2cdd7" />
        {(economy ? [50] : [2.5, 4.5]).map((value) => (
          <g key={value}>
            <path
              d={`M37 ${y(value)}H394`}
              stroke="#9f786d"
              strokeDasharray="4 4"
            />
            <text x="32" y={y(value) + 3} textAnchor="end">
              {economy ? value : `${value}%`}
            </text>
          </g>
        ))}
        {economy && (
          <text x="400" y={secondaryY(0) + 3}>
            0%
          </text>
        )}
        {dateTicks.map((date) => (
          <text
            key={date}
            x={x(date)}
            y="159"
            textAnchor={
              date === first ? "start" : date === last ? "end" : "middle"
            }
          >
            {monthName(date)}
          </text>
        ))}
        {lines.map((line) => {
          const project = line.axis === "secondary" ? secondaryY : y;
          const points = line.points.filter(
            (point) => monthIndex(point.date) >= first,
          );
          const path = points
            .map((point, index) => {
              const prior = points[index - 1];
              const connects =
                prior &&
                monthIndex(point.date) - monthIndex(prior.date) === 1 &&
                point.basis === prior.basis;
              return `${connects ? "L" : "M"}${x(monthIndex(point.date))},${project(point.value)}`;
            })
            .join(" ");
          return (
            <g key={line.code}>
              <path d={path} fill="none" stroke={line.color} strokeWidth="2" />
              {points.map(
                (point, index) =>
                  (index === points.length - 1 ||
                    (line.code === "US_T10_REAL" && point.value > 2.5) ||
                    (!points[index - 1] && !points[index + 1])) && (
                    <circle
                      key={point.date}
                      cx={x(monthIndex(point.date))}
                      cy={project(point.value)}
                      r="2.8"
                      fill={line.color}
                    >
                      <title>
                        {line.label} · {point.date} · {point.value}
                        {line.code === "PMI" ? "" : "%"}
                      </title>
                    </circle>
                  ),
              )}
            </g>
          );
        })}
      </svg>
      <div className="op-cycle-trend-legend">
        {lines.map((line) => {
          const point = line.points.at(-1)!;
          const href = sourceHref(
            data.series?.find((row) => row.code === line.code)?.sourceUri,
          );
          const text = (
            <>
              {line.label}{" "}
              <strong>
                {point.value.toFixed(line.code === "PMI" ? 1 : 2)}
                {line.code === "PMI" ? "" : "%"}
              </strong>
            </>
          );
          return (
            <span key={line.code} title={point.date}>
              <i style={{ background: line.color }} aria-hidden="true" />
              {href ? (
                <a href={href} target="_blank" rel="noreferrer">
                  {text}
                </a>
              ) : (
                text
              )}
            </span>
          );
        })}
      </div>
      <small>
        {economy
          ? "左轴 PMI，右轴同比增幅。统计口径变化处以断点标示。"
          : "红色区域为名义利率高于 4.5%。实际利率高于 2.5% 以圆点标示。"}
      </small>
    </>
  );
}

export default function MacroCycleTrends({
  data,
  supplemental,
  cutoff,
  facts,
  changeBp,
}: {
  data: OpportunityContext;
  supplemental?: OpportunityContext;
  cutoff?: string | null;
  facts: CycleFact[];
  changeBp?: number | null;
}) {
  const fact = (code: string) => facts.find((row) => row.code === code);
  const pmi = fact("PMI");
  const real = fact("US_T10_REAL");
  const nominal = fact("US_T10_NOMINAL");
  const oil = fact("US_BRENT");
  const pmiValue = pmi ? Number(pmi.value) : null;
  const pmiLevel =
    pmiValue === null
      ? null
      : pmiValue < 49.5
        ? 0
        : pmiValue < 50.5
          ? 1
          : pmiValue <= 52
            ? 2
            : 3;
  const flags = [
    real && Number(real.value) > 2.5,
    nominal && Number(nominal.value) > 4.5,
    changeBp != null && changeBp >= 25,
    oil && Number(oil.value) >= 100,
  ].filter(Boolean).length;
  const risk =
    flags >= 2
      ? 2
      : flags === 1
        ? 1
        : real && nominal && changeBp != null && oil
          ? 0
          : null;
  return (
    <div className="op-cycle-trends">
      <section className="op-cycle-trend" aria-label="经济冷热走势图">
        <h4>经济冷热走势图</h4>
        <div className="op-cycle-gauge" aria-label="制造业景气分段">
          {["偏冷收缩", "磨底修复", "温和扩张", "偏热"].map((label, index) => (
            <span key={label} data-active={pmiLevel === index}>
              {label}
              {pmiLevel === index && <strong> · {pmi?.value}</strong>}
            </span>
          ))}
        </div>
        <Trend
          data={data}
          supplemental={supplemental}
          cutoff={cutoff}
          economy
        />
      </section>
      <section className="op-cycle-trend" aria-label="流动性与风险走势图">
        <h4>流动性与风险走势图</h4>
        <div
          className="op-cycle-gauge op-cycle-gauge-risk"
          aria-label="高息与油价压力分段"
        >
          {["低风险", "中等偏紧", "高位警戒"].map((label, index) => (
            <span key={label} data-active={risk === index}>
              {label}
            </span>
          ))}
        </div>
        {risk === null && <small>当前证据不足以给出完整的压力分级。</small>}
        <Trend
          data={data}
          supplemental={supplemental}
          cutoff={cutoff}
          economy={false}
        />
      </section>
    </div>
  );
}

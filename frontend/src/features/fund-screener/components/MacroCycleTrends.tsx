import type { OpportunityContext } from "../../../api/investment";
import type { MacroComposite } from "../../../api/macro-cycle";
import {
  cycleMonth,
  cycleTrend,
  cycleVix,
  liquidityBand,
  vixBand,
  type CycleFact,
} from "../macro-cycle-overview";
import { sourceHref } from "../macro-display";

const activitySeries = [
  { code: "PMI", label: "制造业 PMI", color: "#146556", axis: "primary" },
  { code: "M1_YOY", label: "M1 同比", color: "#9b5d21", axis: "secondary" },
  {
    code: "TSF_STOCK_YOY",
    label: "社融存量同比",
    color: "#376db7",
    axis: "secondary",
  },
] as const;
const inflationSeries = [
  { code: "PPI_YOY", label: "PPI 同比", color: "#a75178", axis: "primary" },
  { code: "CPI_YOY", label: "CPI 同比", color: "#376db7", axis: "primary" },
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
  {
    code: "US_T2_NOMINAL",
    label: "美国 2Y",
    color: "#996219",
    axis: "primary",
  },
] as const;

const monthIndex = cycleMonth;
const monthName = (index: number) =>
  `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;

function Trend({
  data,
  supplemental,
  cutoff,
  kind,
}: {
  data: OpportunityContext;
  supplemental?: OpportunityContext;
  cutoff?: string | null;
  kind: "activity" | "inflation" | "rates";
}) {
  const activity = kind === "activity";
  const inflation = kind === "inflation";
  const readings = (
    activity ? activitySeries : inflation ? inflationSeries : ratesSeries
  )
    .map((line) => ({
      ...line,
      points: cycleTrend(data, line.code, cutoff, supplemental),
    }))
    .filter((line) => line.points.length);
  const lines = readings.filter((line) => line.points.length >= 2);
  const title = activity
    ? "景气与货币活化趋势"
    : inflation
      ? "上下游通胀剪刀差"
      : "中美国债收益率趋势";
  const legend = (
    <div className="op-cycle-trend-legend">
      {readings.map((line) => {
        const point = line.points.at(-1)!;
        const href = sourceHref(
          data.series?.find((row) => row.code === line.code)?.sourceUri ??
            data.items.find((row) => row.code === line.code)?.sourceUri,
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
            {lines.some((display) => display.code === line.code) && (
              <i style={{ background: line.color }} aria-hidden="true" />
            )}
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
  );
  if (!lines.length)
    return (
      <div className="op-cycle-subplot">
        <h5>{title}</h5>
        {legend}
        <p className="op-cycle-missing">可用历史样本不足，显示最新观测。</p>
      </div>
    );
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
  const values = (axis: "primary" | "secondary") =>
    lines
      .filter((line) => line.axis === axis)
      .flatMap((line) =>
        line.points
          .filter((point) => monthIndex(point.date) >= first)
          .map((point) => point.value),
      );
  const pmiValues =
    lines
      .find((line) => line.code === "PMI")
      ?.points.filter((point) => monthIndex(point.date) >= first)
      .map((point) => point.value) ?? [];
  const pmiRadius = Math.max(
    2.5,
    Math.ceil(
      Math.max(0, ...pmiValues.map((value) => Math.abs(value - 50))) * 2,
    ) / 2,
  );
  const y = activity
    ? (value: number) =>
        143 - ((value - 50 + pmiRadius) / (2 * pmiRadius)) * 125
    : scale(
        inflation
          ? [0, ...values("primary")]
          : [0, 2.5, 4.5, ...values("primary")],
      );
  const secondaryValues = values("secondary");
  const secondaryY = scale([0, ...secondaryValues]);
  const percentageTicks = (samples: number[]) => [
    ...new Set([Math.min(0, ...samples), 0, Math.max(0, ...samples)]),
  ];
  const secondaryTicks = percentageTicks(secondaryValues);
  const ticks = activity
    ? [50 - pmiRadius, 50, 50 + pmiRadius]
    : inflation
      ? percentageTicks(values("primary"))
      : [2.5, 4.5];
  const ppi = readings.find((line) => line.code === "PPI_YOY")?.points ?? [];
  const cpi = new Map(
    readings
      .find((line) => line.code === "CPI_YOY")
      ?.points.map((point) => [point.date, point.value]),
  );
  const paired = ppi
    .filter((point) => monthIndex(point.date) >= first && cpi.has(point.date))
    .map(({ date, value }) => ({
      month: monthIndex(date),
      ppi: value,
      cpi: cpi.get(date)!,
    }));
  const latestGap = paired.at(-1);
  const dateTicks = [...new Set([first, Math.floor((first + last) / 2), last])];
  return (
    <div className="op-cycle-subplot">
      <h5>{title}</h5>
      <svg
        className="op-cycle-trend-plot"
        viewBox="0 0 435 165"
        role="img"
        aria-label={title}
      >
        <title>{title}，短缺月以虚线连接，统计口径变化处留空</title>
        {!inflation && (
          <rect
            x="37"
            y="18"
            width="357"
            height={Math.max(0, y(activity ? 50 : 4.5) - 18)}
            fill={activity ? "#e7f4ed" : "#f9e8e6"}
          />
        )}
        {activity && (
          <rect
            x="37"
            y={y(50)}
            width="357"
            height={143 - y(50)}
            fill="#edf2f9"
          />
        )}
        <path d="M37 18V143H394" fill="none" stroke="#c2cdd7" />
        {ticks.map((value) => (
          <g key={value}>
            <path
              d={`M37 ${y(value)}H394`}
              stroke="#9f786d"
              strokeDasharray="4 4"
            />
            <text x="32" y={y(value) + 3} textAnchor="end">
              {activity ? value : `${value}%`}
            </text>
          </g>
        ))}
        {activity &&
          secondaryValues.length > 0 &&
          secondaryTicks.map((value) => (
            <text key={value} x="400" y={secondaryY(value) + 3}>
              {value}%
            </text>
          ))}
        {paired.map((point, i) => {
          const prior = paired[i - 1];
          return prior && point.month - prior.month === 1 ? (
            <polygon
              key={point.month}
              data-macro-spread="PPI_CPI"
              points={`${x(prior.month)},${y(prior.ppi)} ${x(point.month)},${y(point.ppi)} ${x(point.month)},${y(point.cpi)} ${x(prior.month)},${y(prior.cpi)}`}
              fill="#eacbcc"
              opacity="0.6"
            />
          ) : null;
        })}
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
          const gaps = points.flatMap((point, index) => {
            const prior = points[index - 1];
            if (!prior || point.basis !== prior.basis) return [];
            const distance = monthIndex(point.date) - monthIndex(prior.date);
            return distance > 1 && distance <= 3
              ? [
                  `M${x(monthIndex(prior.date))},${project(prior.value)} L${x(monthIndex(point.date))},${project(point.value)}`,
                ]
              : [];
          });
          return (
            <g key={line.code} data-macro-series={line.code}>
              <path d={path} fill="none" stroke={line.color} strokeWidth="2" />
              {!!gaps.length && (
                <path
                  d={gaps.join(" ")}
                  fill="none"
                  stroke={line.color}
                  strokeWidth="1.7"
                  strokeDasharray="4 3"
                  data-macro-gap={line.code}
                />
              )}
              {points.map(
                (point, index) =>
                  (index === points.length - 1 ||
                    (line.code === "US_T10_REAL" && point.value > 2.5) ||
                    [points[index - 1], points[index + 1]].every(
                      (neighbor) =>
                        !neighbor ||
                        neighbor.basis !== point.basis ||
                        Math.abs(
                          monthIndex(neighbor.date) - monthIndex(point.date),
                        ) > 3,
                    )) && (
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
      {legend}
      {latestGap && (
        <p className="op-cycle-spread">
          {monthName(latestGap.month)} · PPI−CPI{" "}
          {(latestGap.ppi - latestGap.cpi).toFixed(2)} 个百分点
        </p>
      )}
      <small>
        {activity
          ? "左轴 PMI，右轴同比。缺月以虚线连接，口径变更处断开。"
          : inflation
            ? "阴影为相邻月份的 PPI−CPI 差值。"
            : "虚线横线标示利率观察阈值，圆点标出高于 2.5% 的实际利率。"}
      </small>
    </div>
  );
}

const vixLabels = ["波动平稳", "分歧波动", "恐慌升温", "极度恐慌"];

function VixTrend({
  data,
  cutoff,
}: {
  data: OpportunityContext;
  cutoff?: string | null;
}) {
  const vix = cycleVix(data, cutoff);
  const value = vix.latest?.[1];
  const href = sourceHref(vix.sourceUri);
  const level = value === undefined ? null : vixBand(value);
  const dates = vix.points.map(([day]) => Date.parse(day));
  const start = dates[0] ?? 0;
  const end = dates.at(-1) ?? start;
  const upper = Math.max(40, ...vix.points.map(([, point]) => point)) + 2;
  const x = (day: number) =>
    33 + ((day - start) / Math.max(1, end - start)) * 365;
  const y = (point: number) => 138 - (point / upper) * 118;
  const plot = vix.points
    .map(
      ([, point], index) =>
        `${index ? "L" : "M"}${x(dates[index])},${y(point)}`,
    )
    .join(" ");
  return (
    <section className="op-cycle-trend" aria-label="VIX 市场波动">
      <h4>VIX 市场波动</h4>
      <div
        className="op-cycle-gauge op-cycle-gauge-risk"
        aria-label="VIX 波动区间"
      >
        {vixLabels.map((label, index) => (
          <span key={label} data-active={level === index}>
            {label}
          </span>
        ))}
      </div>
      {vix.points.length >= 4 && (
        <svg
          className="op-cycle-trend-plot"
          viewBox="0 0 435 165"
          role="img"
          aria-label="VIX 近两年收盘走势"
        >
          {[15, 25, 35].map((threshold) => (
            <g key={threshold}>
              <path
                d={`M33 ${y(threshold)}H398`}
                stroke="#a18d84"
                strokeDasharray="4 3"
              />
              <text x="29" y={y(threshold) + 3} textAnchor="end">
                {threshold}
              </text>
            </g>
          ))}
          <path d={plot} fill="none" stroke="#65538b" strokeWidth="1.6" />
          <circle cx={x(end)} cy={y(value!)} r="2.8" fill="#65538b" />
          <text x="33" y="157">
            {vix.points[0][0].slice(0, 7)}
          </text>
          <text x="398" y="157" textAnchor="end">
            {vix.points.at(-1)![0].slice(0, 7)}
          </text>
        </svg>
      )}
      {value !== undefined ? (
        <p className="op-cycle-vix-value">
          <strong>{value.toFixed(2)} 点</strong> · {vix.latest?.[0]} 收盘{" "}
          {href && (
            <a href={href} target="_blank" rel="noreferrer">
              Cboe / FRED
            </a>
          )}
        </p>
      ) : (
        <p className="op-cycle-missing">该窗口暂无可用 VIX 收盘数据。</p>
      )}
      <small>标普 500 期权隐含的近一个月波动预期。分档用于观察波动程度。</small>
    </section>
  );
}

export default function MacroCycleTrends({
  data,
  supplemental,
  cutoff,
  facts,
  rateState,
  composite,
}: {
  data: OpportunityContext;
  supplemental?: OpportunityContext;
  cutoff?: string | null;
  facts: CycleFact[];
  rateState?: string;
  composite?: MacroComposite;
}) {
  const fact = (code: string) => facts.find((row) => row.code === code);
  const pmi = fact("PMI");
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
  const liquidity = liquidityBand(facts, rateState);
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
        {!!composite?.labels.length && (
          <p className="op-cycle-composite">{composite.labels.join(" · ")}</p>
        )}
        <Trend
          data={data}
          supplemental={supplemental}
          cutoff={cutoff}
          kind="activity"
        />
        <Trend
          data={data}
          supplemental={supplemental}
          cutoff={cutoff}
          kind="inflation"
        />
      </section>
      <section className="op-cycle-trend" aria-label="流动性松紧">
        <h4>全球流动性压力与风险走势图</h4>
        <div className="op-cycle-gauge" aria-label="流动性松紧分段">
          {["充裕宽松", "中性平衡", "偏紧收敛", "紧缩承压"].map(
            (label, index) => (
              <span key={label} data-active={liquidity === index}>
                {label}
              </span>
            ),
          )}
        </div>
        {liquidity === null && (
          <small>至少需要两组利率与货币信号才能分档。</small>
        )}
        <Trend
          data={data}
          supplemental={supplemental}
          cutoff={cutoff}
          kind="rates"
        />
      </section>
      <VixTrend data={data} cutoff={cutoff} />
    </div>
  );
}

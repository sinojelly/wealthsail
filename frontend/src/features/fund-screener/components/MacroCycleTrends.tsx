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
  { code: "PMI", label: "制造业 PMI", color: "#146556" },
] as const;
const moneySeries = [
  { code: "M1_YOY", label: "M1 同比", color: "#9b5d21" },
  { code: "M2_YOY", label: "M2 同比", color: "#146556" },
  {
    code: "TSF_STOCK_YOY",
    label: "社融存量同比",
    color: "#376db7",
  },
] as const;
const inflationSeries = [
  { code: "PPI_YOY", label: "PPI 同比", color: "#a75178" },
  { code: "CPI_YOY", label: "CPI 同比", color: "#376db7" },
] as const;
const ratesSeries = [
  {
    code: "US_T10_NOMINAL",
    label: "美国 10Y 名义",
    color: "#b3453c",
  },
  {
    code: "US_T10_REAL",
    label: "美国 10Y 实际",
    color: "#8261a8",
  },
  { code: "CN_T10", label: "中国 10Y", color: "#167466" },
  {
    code: "US_T2_NOMINAL",
    label: "美国 2Y",
    color: "#996219",
  },
] as const;

const chartKinds = {
  activity: { title: "制造业 PMI 历史走势", series: activitySeries },
  money: { title: "社融、M2 与 M1 历史走势", series: moneySeries },
  inflation: { title: "PPI 与 CPI 历史走势", series: inflationSeries },
  rates: { title: "中美国债收益率趋势", series: ratesSeries },
};

const monthIndex = cycleMonth;
const monthName = (index: number) =>
  `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;

function comparable(
  code: string,
  before: { date: string; basis: string },
  after: { date: string; basis: string },
) {
  return (
    before.basis === after.basis &&
    (code !== "M1_YOY" ||
      before.date >= "2025-01" ||
      after.date < "2025-01" ||
      after.basis === "PBOC_M1_2025")
  );
}

function Trend({
  data,
  supplemental,
  cutoff,
  kind,
}: {
  data: OpportunityContext;
  supplemental?: OpportunityContext;
  cutoff?: string | null;
  kind: keyof typeof chartKinds;
}) {
  const activity = kind === "activity";
  const { title, series } = chartKinds[kind];
  const readings = series
    .map((line) => ({
      ...line,
      points: cycleTrend(data, line.code, cutoff, supplemental),
    }))
    .filter((line) => line.points.length);
  const lines = readings.filter((line) => line.points.length >= 2);
  const legend = (
    <div className="op-cycle-trend-legend" aria-label={`${title}图例`}>
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
          <span key={line.code}>
            <i style={{ background: line.color }} aria-hidden="true" />
            {href ? (
              <a href={href} target="_blank" rel="noreferrer">
                {text}
              </a>
            ) : (
              text
            )}
            <small>
              {point.date}
              {point.providerOnly && " · 供应商读数"}
            </small>
          </span>
        );
      })}
    </div>
  );
  if (!lines.length)
    return (
      <div className="op-cycle-subplot">
        {legend}
        <p className="op-cycle-missing">
          {readings.length
            ? "历史样本待补齐，图例显示最近读数。"
            : "该窗口暂无可用历史数据。"}
        </p>
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
  const values = lines.flatMap((line) =>
    line.points
      .filter((point) => monthIndex(point.date) >= first)
      .map((point) => point.value),
  );
  const pmiRadius = Math.max(
    2.5,
    Math.ceil(Math.max(0, ...values.map((value) => Math.abs(value - 50))) * 2) /
      2,
  );
  const y = activity
    ? (value: number) =>
        143 - ((value - 50 + pmiRadius) / (2 * pmiRadius)) * 125
    : scale(kind === "rates" ? [0, 2, 4.5, ...values] : [0, ...values]);
  const percentageTicks = (samples: number[]) => [
    ...new Set([Math.min(0, ...samples), 0, Math.max(0, ...samples)]),
  ];
  const ticks = activity
    ? [50 - pmiRadius, 50, 50 + pmiRadius]
    : kind === "rates"
      ? [2, 4.5]
      : percentageTicks(values);
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
      <svg
        className="op-cycle-trend-plot"
        viewBox="0 0 435 165"
        role="img"
        aria-label={title}
      >
        <title>{title}，短缺月以虚线连接，统计口径变化处留空</title>
        {(activity || kind === "rates") && (
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
          const project = y;
          const points = line.points.filter(
            (point) => monthIndex(point.date) >= first,
          );
          const path = points
            .map((point, index) => {
              const prior = points[index - 1];
              const connects =
                prior &&
                monthIndex(point.date) - monthIndex(prior.date) === 1 &&
                comparable(line.code, prior, point);
              return `${connects ? "L" : "M"}${x(monthIndex(point.date))},${project(point.value)}`;
            })
            .join(" ");
          const gaps = points.flatMap((point, index) => {
            const prior = points[index - 1];
            if (!prior || !comparable(line.code, prior, point)) return [];
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
                    (line.code === "US_T10_REAL" && point.value >= 2) ||
                    [points[index - 1], points[index + 1]].every(
                      (neighbor) =>
                        !neighbor ||
                        !comparable(
                          line.code,
                          neighbor.date < point.date ? neighbor : point,
                          neighbor.date < point.date ? point : neighbor,
                        ) ||
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
      {readings.some((line) =>
        line.points.some((point) => point.providerOnly),
      ) && <small>部分月份采用东方财富采集值。</small>}
      {latestGap && (
        <p className="op-cycle-spread">
          {monthName(latestGap.month)} · PPI−CPI{" "}
          {(latestGap.ppi - latestGap.cpi).toFixed(2)} 个百分点
        </p>
      )}
      {kind === "money" && (
        <small>缺月以虚线连接，M1 统计口径变更处断开。</small>
      )}
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
    <section className="op-cycle-subplot" aria-label="VIX 市场波动">
      <h5>VIX 市场波动</h5>
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
      {vix.points.length >= 2 && (
        <svg
          className="op-cycle-trend-plot"
          viewBox="0 0 435 165"
          role="img"
          aria-label="VIX 近两年收盘走势"
        >
          {[15, 20, 30].map((threshold) => (
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
  const moneyGap = fact("M1_M2_GAP");
  const cpi = fact("CPI_YOY");
  const ppi = fact("PPI_YOY");
  const nominal = fact("US_T10_NOMINAL");
  const real = fact("US_T10_REAL");
  const vix = cycleVix(data, cutoff).latest?.[1];
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
        <h4>🌡️ 国内经济景气度</h4>
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
        <Guide>
          50 是景气荣枯线。高于 50 表示更多制造企业报告环比扩张，高于 51
          可观察扩张是否加快。低于 50 表示偏弱
          {pmi && `，最近读数为 ${pmi.value}`}。
        </Guide>
      </section>
      <section className="op-cycle-trend" aria-label="国内货币与信用脉冲">
        <h4>🏦 国内货币与信用脉冲</h4>
        <Trend
          data={data}
          supplemental={supplemental}
          cutoff={cutoff}
          kind="money"
        />
        <Guide>
          社融与 M2 同比越高，通常代表融资或广义货币扩张越快，8.5%
          是观察参考。M1 增速高于 M2 时，可观察活期资金能否带动企业支出
          {moneyGap && `。同口径最近价差为 ${moneyGap.value} 个百分点`}。
        </Guide>
      </section>
      <section
        className="op-cycle-trend"
        aria-label="国内物价与上下游利润剪刀差"
      >
        <h4>🏭 国内物价与上下游利润剪刀差</h4>
        <Trend
          data={data}
          supplemental={supplemental}
          cutoff={cutoff}
          kind="inflation"
        />
        <Guide>
          CPI 同比在 1% 到 3% 间通常属温和区间，低于 0.5% 可关注终端需求。PPI
          明显高于 CPI
          时，留意哪些中下游企业能把上游成本传给客户。阴影显示同月价差
          {ppi && cpi && `，最新 PPI ${ppi.value}%，CPI ${cpi.value}%`}。
        </Guide>
      </section>
      <section className="op-cycle-trend" aria-label="流动性松紧">
        <h4>🌍 全球流动性水位与市场波动</h4>
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
        <VixTrend data={data} cutoff={cutoff} />
        <Guide>
          美债 10 年名义利率在 4.5% 以上或实际利率在 2%
          以上时，黄金持有成本和科技估值更值得观察。VIX 低于 15 通常较平稳，超过
          20、尤其超过 30 时波动预期抬升
          {(nominal || real || vix !== undefined) &&
            `。最新 ${[nominal && `名义 ${nominal.value}%`, real && `实际 ${real.value}%`, vix !== undefined && `VIX ${vix.toFixed(2)}`].filter(Boolean).join("、")}`}
          。
        </Guide>
      </section>
    </div>
  );
}

function Guide({ children }: { children: React.ReactNode }) {
  return (
    <p className="op-cycle-guide">
      <strong>💡 读图指南 </strong>
      {children}
    </p>
  );
}

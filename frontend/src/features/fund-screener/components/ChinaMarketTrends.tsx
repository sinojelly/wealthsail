import type { OpportunitySeries } from "../../../api/investment";
import { sourceHref } from "../macro-display";

const observationDay = 86_400_000;
const marketWindow = observationDay * 731;
const format = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });

type DailySample = { date: string; value: number; day: number };

function dailySamples(
  series: OpportunitySeries | undefined,
  cutoff?: string | null,
) {
  const points = new Map<string, DailySample>();
  for (const row of series?.history ?? []) {
    const date = row.date.slice(0, 10);
    const day = Date.parse(date);
    const available = row.availableAt ?? row.retrievedAt;
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
      !Number.isFinite(day) ||
      !Number.isFinite(row.value) ||
      row.value! <= 0 ||
      !["OBSERVED", "READY", "STALE"].includes(row.state) ||
      (cutoff &&
        (date > cutoff.slice(0, 10) ||
          !available ||
          !(Date.parse(available) <= Date.parse(cutoff))))
    )
      continue;
    points.set(date, { date, day, value: Number(row.value) });
  }
  const ordered = [...points.values()].sort((a, b) => a.day - b.day);
  const latest = ordered.at(-1);
  return latest
    ? ordered.filter((point) => point.day >= latest.day - marketWindow)
    : [];
}

function percentile(points: DailySample[]) {
  const latest = points.at(-1);
  if (
    !latest ||
    points.length < 400 ||
    latest.day - points[0].day < 690 * observationDay
  )
    return null;
  const below = points.filter((point) => point.value < latest.value).length;
  const equal = points.filter((point) => point.value === latest.value).length;
  return (100 * (below + equal / 2)) / points.length;
}

function turnoverBand(points: DailySample[]) {
  const recent = points
    .slice(-61, -1)
    .map((point) => point.value)
    .sort((a, b) => a - b);
  if (recent.length < 20) return null;
  const low = recent[Math.floor((recent.length - 1) * 0.2)];
  const high = recent[Math.floor((recent.length - 1) * 0.8)];
  const latest = points.at(-1)!.value;
  return {
    low,
    high,
    label: latest < low ? "缩量" : latest > high ? "放量" : "常态",
    count: recent.length,
  };
}

function DailyPlot({
  points,
  color,
  label,
}: {
  points: DailySample[];
  color: string;
  label: string;
}) {
  const latest = points.at(-1)!;
  const first = points[0];
  const values = points.map((point) => point.value);
  const bottom = Math.min(...values);
  const top = Math.max(...values);
  const pad = Math.max((top - bottom) * 0.08, latest.value * 0.001);
  const px = (point: DailySample) =>
    46 + 350 * ((point.day - first.day) / (latest.day - first.day));
  const py = (value: number) =>
    110 - 94 * ((value - bottom + pad) / (top - bottom + 2 * pad));
  const path = points
    .map(
      (point, i) =>
        `${i && point.day - points[i - 1].day <= 8 * observationDay ? "L" : "M"} ${px(point)},${py(point.value)}`,
    )
    .join(" ");
  return (
    <svg
      className="op-cycle-trend-plot"
      viewBox="0 0 420 137"
      role="img"
      aria-label={label}
    >
      <path d="M46 12V110H396" fill="none" stroke="#c2cdd7" />
      {[...new Set([bottom, top])].map((value) => (
        <g key={value}>
          <path
            d={`M46 ${py(value)}H396`}
            stroke="#dce5ed"
            strokeDasharray="3 3"
          />
          <text x="42" y={py(value) + 3} textAnchor="end">
            {format.format(value)}
          </text>
        </g>
      ))}
      <path d={path} fill="none" stroke={color} strokeWidth="2" />
      {points.map(
        (point, i) =>
          (i === points.length - 1 ||
            ((!points[i - 1] ||
              point.day - points[i - 1].day > 8 * observationDay) &&
              (!points[i + 1] ||
                points[i + 1].day - point.day > 8 * observationDay))) && (
            <circle
              key={point.date}
              cx={px(point)}
              cy={py(point.value)}
              r="2.7"
              fill={color}
            >
              <title>
                {point.date} {format.format(point.value)}
              </title>
            </circle>
          ),
      )}
      <text x="46" y="129">
        {first.date}
      </text>
      <text x="396" y="129" textAnchor="end">
        {latest.date}
      </text>
    </svg>
  );
}

function DailyReading({
  series,
  cutoff,
  label,
  color,
  index,
}: {
  series?: OpportunitySeries;
  cutoff?: string | null;
  label: string;
  color: string;
  index?: boolean;
}) {
  const points = dailySamples(series, cutoff);
  const latest = points.at(-1);
  const position = index ? percentile(points) : null;
  const volume = index ? null : turnoverBand(points);
  const href = sourceHref(series?.sourceUri);
  if (!latest)
    return <p className="op-cycle-missing">{label}暂无可核对的收盘数据。</p>;
  return (
    <div className="op-cycle-subplot">
      <p>
        <strong>
          {label} {format.format(latest.value)} {series?.unit}
        </strong>{" "}
        <small>
          {latest.date}
          {series?.state === "STALE" && " · 历史读数"}
        </small>
      </p>
      {index && (
        <p className="op-cycle-market-percentile">
          {position == null
            ? "近两年历史样本不足，暂不计算分位"
            : `近两年收盘分位 ${position.toFixed(1)}%`}
        </p>
      )}
      {volume && (
        <p className="op-cycle-market-percentile">
          {volume.label} · 参考前 {volume.count} 个观测的 20% 至 80% 区间{" "}
          {format.format(volume.low)}–{format.format(volume.high)} 亿元
        </p>
      )}
      {points.length >= 2 ? (
        <DailyPlot points={points} color={color} label={`${label}日线走势`} />
      ) : (
        <p className="op-cycle-missing">仅有最近读数，走势待补齐。</p>
      )}
      {href && (
        <a href={href} target="_blank" rel="noreferrer">
          {series?.sourceName ?? "数据来源"}
        </a>
      )}
    </div>
  );
}

export function DomesticIndexTrends({
  rows,
  cutoff,
}: {
  rows?: OpportunitySeries[];
  cutoff?: string | null;
}) {
  return (
    <>
      <DailyReading
        series={rows?.find((row) => row.code === "CN_SH_COMP")}
        label="上证指数"
        cutoff={cutoff}
        index
        color="#146556"
      />
      <DailyReading
        series={rows?.find((row) => row.code === "CN_CSI300")}
        label="沪深300"
        cutoff={cutoff}
        index
        color="#376db7"
      />
    </>
  );
}

export function TurnoverTrend({
  rows,
  cutoff,
}: {
  rows?: OpportunitySeries[];
  cutoff?: string | null;
}) {
  return (
    <DailyReading
      series={rows?.find((row) => row.code === "CN_A_TURNOVER")}
      label="沪深京 A 股成交额"
      cutoff={cutoff}
      color="#8e546f"
    />
  );
}

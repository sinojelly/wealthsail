import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { FundNavPeriod } from "../../../api/funds";
import type { ActionableZoneTrack } from "../../../api/investment";
import { timeLabel } from "../macro-display";
import { useFundNavHistory } from "../nav-history-resource";

const periods: Array<[FundNavPeriod, string]> = [
  ["6m", "近半年"],
  ["1y", "近1年"],
  ["2y", "近2年"],
  ["all", "成立以来"],
];
const number = (value: unknown) =>
  value != null && Number.isFinite(Number(value)) && Number(value) > 0
    ? Number(value)
    : null;
const price = (value: unknown) => number(value)?.toFixed(4) ?? "—";
function Trend({
  fundCode,
  track,
}: {
  fundCode: string;
  track?: ActionableZoneTrack;
}) {
  const [period, setPeriod] = useState<FundNavPeriod>("2y");
  const { data, loading, failed, exhausted, retry } = useFundNavHistory(
    fundCode,
    period,
  );
  const points =
    data?.points.map((point) => ({ ...point, value: Number(point.unitNav) })) ??
    [];
  const latestPoint = points.at(-1);
  const low = number(track?.entryZoneMin),
    high = number(track?.entryZoneMax),
    stop = number(track?.stopLossPrice),
    sellLow = number(track?.sellZoneMin ?? track?.takeProfitT1),
    sellHigh = number(track?.sellZoneMax ?? track?.takeProfitT2);
  const completeBand = low != null && high != null && low <= high;
  const completeSellBand =
    sellLow != null && sellHigh != null && sellLow <= sellHigh;
  const values = [
    ...points.map((point) => point.value),
    ...(completeBand ? [low!, high!] : []),
    ...(stop != null ? [stop] : []),
    ...(sellLow != null ? [sellLow] : []),
    ...(sellHigh != null ? [sellHigh] : []),
  ];
  const minimum = values.length ? Math.min(...values) : 0,
    maximum = values.length ? Math.max(...values) : 1;
  const padding = Math.max((maximum - minimum) * 0.06, maximum * 0.01);
  const percentile = data?.summary.percentilePct;
  return (
    <section className="op-nav-trend" aria-label={`${fundCode}单位净值图`}>
      <header>
        <strong>单位净值走势</strong>
        <div className="op-nav-periods" aria-label="净值观察周期">
          {periods.map(([key, label]) => (
            <button
              type="button"
              key={key}
              aria-pressed={period === key}
              onClick={() => setPeriod(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </header>
      {points.length ? (
        <>
          <p className="op-nav-summary">
            图中最新净值：{timeLabel(latestPoint!.date)} ·{" "}
            {price(latestPoint!.unitNav)} 元/份
          </p>
          <div
            className="op-nav-plot"
            role="img"
            aria-label={`${fundCode}单位净值走势`}
          >
            <ResponsiveContainer
              width="100%"
              height="100%"
              initialDimension={{ width: 320, height: 220 }}
            >
              <LineChart
                data={points}
                margin={{ top: 10, right: 8, bottom: 0, left: -18 }}
              >
                <CartesianGrid
                  vertical={false}
                  stroke="#e2e8f0"
                  strokeDasharray="3 3"
                />
                <XAxis
                  dataKey="date"
                  padding={{ right: 24 }}
                  tickFormatter={(date: string) => date.slice(5)}
                  minTickGap={38}
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                />
                <YAxis
                  domain={[Math.max(0, minimum - padding), maximum + padding]}
                  tickFormatter={(value: number) => value.toFixed(2)}
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  width={56}
                />
                <Tooltip
                  labelFormatter={(date) => timeLabel(String(date))}
                  formatter={(_value, _name, item) => [
                    `${price(item.payload?.unitNav)} 元/份`,
                    "单位净值",
                  ]}
                />
                {completeBand && (
                  <ReferenceArea
                    y1={low!}
                    y2={high!}
                    fill="#14b89a"
                    fillOpacity={0.14}
                    strokeOpacity={0}
                  />
                )}
                {completeSellBand && (
                  <ReferenceArea
                    y1={sellLow!}
                    y2={sellHigh!}
                    fill="#f97316"
                    fillOpacity={0.18}
                    strokeOpacity={0}
                  />
                )}
                {stop != null && (
                  <ReferenceLine
                    y={stop}
                    stroke="#d14343"
                    strokeDasharray="5 4"
                  />
                )}
                {!completeSellBand && sellLow != null && (
                  <ReferenceLine
                    y={sellLow}
                    stroke="#d97706"
                    strokeDasharray="5 4"
                  />
                )}
                {!completeSellBand && sellHigh != null && (
                  <ReferenceLine
                    y={sellHigh}
                    stroke="#d97706"
                    strokeDasharray="5 4"
                  />
                )}
                <Line
                  dataKey="value"
                  name="单位净值"
                  type="linear"
                  stroke="#087e8b"
                  strokeWidth={1.8}
                  dot={false}
                  activeDot={{ r: 3 }}
                  isAnimationActive={false}
                />
                <ReferenceDot
                  x={latestPoint!.date}
                  y={latestPoint!.value}
                  r={3}
                  fill="#087e8b"
                  stroke="#fff"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="op-nav-legend">
            {completeBand && <span className="buy">建议买入区间</span>}
            {stop != null && <span className="stop">止损参考</span>}
            {completeSellBand && <span className="sell">建议卖出区间</span>}
            {!completeSellBand && sellLow != null && (
              <span className="target">卖出参考下沿（T1）</span>
            )}
            {!completeSellBand && sellHigh != null && (
              <span className="target">卖出参考上沿（T2）</span>
            )}
          </div>
          <p className="op-nav-summary">
            区间最低 {price(data?.summary.minimumNav)} · 最高{" "}
            {price(data?.summary.maximumNav)} · 当前处于该周期{" "}
            {percentile != null &&
            Number.isFinite(percentile) &&
            percentile >= 0 &&
            percentile <= 100
              ? `${percentile.toFixed(1)}%`
              : "未知"}{" "}
            分位
          </p>
        </>
      ) : (
        <p className="op-nav-empty" role="status">
          {loading
            ? "真实单位净值加载中…"
            : failed || data?.state === "UNAVAILABLE"
              ? "净值走势暂不可用"
              : "暂无可用的单位净值记录"}
        </p>
      )}
      {(data?.refresh.state === "QUEUED" ||
        data?.refresh.state === "RUNNING") && (
        <p role="status">
          历史净值正在补齐
          {exhausted ? "，请稍后重新读取。" : "，当前保留已有真实记录。"}
        </p>
      )}
      {(failed || data?.refresh.state === "FAILED") && points.length > 0 && (
        <p role="status">补齐暂未完成，保留已确认净值。</p>
      )}
      {period === "all" && data?.coverage.fullHistory && (
        <small>
          来源完整披露历史，始于{timeLabel(data.coverage.startDate)}
        </small>
      )}
      {data && !data.coverage.fullHistory && period === "all" && (
        <small>
          当前仅覆盖{timeLabel(data.coverage.startDate)}起的
          {data.coverage.sampleCount}个真实样本，完整披露历史待补齐。
        </small>
      )}
      {(failed || exhausted || data?.refresh.state === "FAILED") && (
        <button
          type="button"
          className="op-nav-retry"
          disabled={loading}
          onClick={retry}
        >
          重新读取净值
        </button>
      )}
    </section>
  );
}
export default function FundNavTrendChart(props: {
  fundCode: string;
  track?: ActionableZoneTrack;
}) {
  if (!/^\d{6}$/.test(props.fundCode)) return null;
  return <Trend key={props.fundCode} {...props} />;
}

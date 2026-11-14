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
import type { FundNavHistory, FundNavPeriod } from "../../../api/funds";
import type { ActionableZoneTrack } from "../../../api/investment";
import { sourceHref, timeLabel } from "../macro-display";
import { useFundNavHistory } from "../nav-history-resource";
import { navPercentile } from "../nav-percentile";

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
function TrendView({
  fundCode,
  track,
  confirmedNavAsOf,
  period,
  onPeriodChange,
  history,
  historical = false,
  allPeriodLabel,
}: {
  fundCode: string;
  track?: ActionableZoneTrack;
  confirmedNavAsOf?: string;
  period: FundNavPeriod;
  onPeriodChange: (period: FundNavPeriod) => void;
  history: ReturnType<typeof useFundNavHistory>;
  historical?: boolean;
  allPeriodLabel?: string;
}) {
  const { data, loading, failed, exhausted, retry } = history;
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
  const percentile =
    data?.summary.percentilePct ??
    navPercentile(data?.points.map((point) => Number(point.unitNav)) ?? []);
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
              onClick={() => onPeriodChange(key)}
            >
              {key === "all"
                ? historical
                  ? "冻结全窗口"
                  : (allPeriodLabel ?? label)
                : label}
            </button>
          ))}
        </div>
      </header>
      {sourceHref(data?.sourceUri) && (
        <small>
          <a
            href={sourceHref(data?.sourceUri)}
            target="_blank"
            rel="noreferrer"
          >
            曲线数据来源
          </a>
        </small>
      )}
      {points.length ? (
        <>
          <p className="op-nav-summary">
            {historical ? "研究时曲线末点" : "图中最新净值"}：
            {timeLabel(latestPoint!.date)} · {price(latestPoint!.unitNav)} 元/份
          </p>
          {confirmedNavAsOf &&
            latestPoint!.date < confirmedNavAsOf.slice(0, 10) && (
              <p className="op-nav-summary" role="status">
                曲线末点早于最新确认净值：曲线截至{timeLabel(latestPoint!.date)}
                ， 确认净值截至{timeLabel(confirmedNavAsOf)}
                ；较新的净值尚未纳入曲线。
              </p>
            )}
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
                  domain={[minimum * 0.97, maximum * 1.03]}
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
                    fill="#ef4444"
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
          <div className="op-nav-footer" aria-label="净值区间说明与图例">
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
              {price(data?.summary.maximumNav)} ·{" "}
              {historical ? "研究时处于" : "当前处于"}该周期{" "}
              {percentile != null &&
              Number.isFinite(percentile) &&
              percentile >= 0 &&
              percentile <= 100
                ? `${percentile.toFixed(1)}%`
                : "未知"}{" "}
              分位
            </p>
          </div>
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
      {(failed ||
        data?.refresh.state === "FAILED" ||
        data?.refresh.state === "PARTIAL") &&
        points.length > 0 && (
          <p role="status">补齐暂未完成，保留已确认净值。</p>
        )}
      {period === "all" && data?.coverage.fullHistory && (
        <small>
          来源完整披露历史，始于{timeLabel(data.coverage.startDate)}
        </small>
      )}
      {data && !data.coverage.fullHistory && period === "all" && (
        <small>
          {historical ? "冻结窗口仅覆盖" : "当前仅覆盖"}
          {timeLabel(data.coverage.startDate)}起的
          {data.coverage.sampleCount}个真实样本，
          {historical ? "此页不会补抓其他历史数据。" : "完整披露历史待补齐。"}
        </small>
      )}
      {(failed ||
        exhausted ||
        (!historical && !data && !loading) ||
        (!historical &&
          data &&
          data.state !== "READY" &&
          data.refresh.state !== "QUEUED" &&
          data.refresh.state !== "RUNNING") ||
        data?.refresh.state === "FAILED" ||
        data?.refresh.state === "PARTIAL") && (
        <button
          type="button"
          className="op-nav-retry"
          disabled={historical || loading}
          onClick={retry}
        >
          刷新并补齐净值
        </button>
      )}
    </section>
  );
}
type Props = {
  fundCode: string;
  track?: ActionableZoneTrack;
  confirmedNavAsOf?: string;
  snapshot?: FundNavHistory | null;
  snapshotMode?: "HISTORICAL" | "CURRENT_LOCAL";
};
function LiveTrend(props: Props) {
  const [period, setPeriod] = useState<FundNavPeriod>("2y");
  const history = useFundNavHistory(props.fundCode, period);
  return (
    <TrendView
      {...props}
      period={period}
      onPeriodChange={setPeriod}
      history={history}
    />
  );
}
function snapshotProjection(
  snapshot: Props["snapshot"],
  period: FundNavPeriod,
  currentLocal: boolean,
) {
  const end = snapshot?.asOf ?? snapshot?.points.at(-1)?.date;
  const start = end ? new Date(`${end.slice(0, 10)}T00:00:00Z`) : null;
  if (start && period !== "all")
    start.setUTCMonth(
      start.getUTCMonth() - { "6m": 6, "1y": 12, "2y": 24 }[period],
    );
  const points =
    snapshot?.points.filter(
      (point) =>
        !start ||
        period === "all" ||
        point.date >= start.toISOString().slice(0, 10),
    ) ?? [];
  const values = points.map((point) => Number(point.unitNav));
  return snapshot
    ? {
        ...snapshot,
        period,
        points,
        coverage: {
          ...snapshot.coverage,
          startDate: points[0]?.date ?? null,
          endDate: points.at(-1)?.date ?? null,
          sampleCount: points.length,
          fullHistory:
            currentLocal && period === "all" && snapshot.coverage.fullHistory,
        },
        summary: {
          ...snapshot.summary,
          minimumNav: values.length ? String(Math.min(...values)) : null,
          maximumNav: values.length ? String(Math.max(...values)) : null,
          currentNav: points.at(-1)?.unitNav ?? null,
          percentilePct: navPercentile(values),
          percentileMethod: "EMPIRICAL_MIDRANK_V1",
        },
        refresh: { state: "IDLE" as const, runId: null, pollAfterMs: null },
      }
    : undefined;
}
function FrozenTrend({ snapshot, ...props }: Props) {
  const [period, setPeriod] = useState<FundNavPeriod>("2y");
  const data = snapshotProjection(snapshot, period, false);
  return (
    <TrendView
      {...props}
      historical
      period={period}
      onPeriodChange={setPeriod}
      history={{
        data,
        loading: false,
        failed: false,
        exhausted: false,
        retry: () => undefined,
      }}
    />
  );
}
function CurrentLocalTrend({ snapshot, ...props }: Props) {
  const [period, setPeriod] = useState<FundNavPeriod>("2y");
  const [active, setActive] = useState(false);
  const history = useFundNavHistory(props.fundCode, period, active);
  const data = snapshotProjection(snapshot, period, true);
  return (
    <TrendView
      {...props}
      historical={false}
      period={period}
      onPeriodChange={setPeriod}
      allPeriodLabel={
        !active && !snapshot?.coverage.fullHistory ? "本地全窗口" : undefined
      }
      history={{
        ...(active
          ? history
          : { loading: false, failed: false, exhausted: false }),
        data: active ? (history.data ?? data) : data,
        retry: () => {
          history.retry();
          setActive(true);
        },
      }}
    />
  );
}
export default function FundNavTrendChart(props: Props) {
  if (!/^\d{6}$/.test(props.fundCode)) return null;
  return props.snapshot !== undefined &&
    props.snapshotMode === "CURRENT_LOCAL" ? (
    <CurrentLocalTrend key={props.fundCode} {...props} />
  ) : props.snapshot !== undefined ? (
    <FrozenTrend key={props.fundCode} {...props} />
  ) : (
    <LiveTrend key={props.fundCode} {...props} />
  );
}

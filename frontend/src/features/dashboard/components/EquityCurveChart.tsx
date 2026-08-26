import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { toChartNumber } from "../../../domain/chart-number-adapter";
import type { NavPoint } from "../../../api/types";

export default function EquityCurveChart({
  points,
  loading,
}: {
  points: NavPoint[];
  loading?: boolean;
}) {
  const chartData = points.filter((point) => point.totalNav !== null).map((point) => ({
    date: point.date.slice(5), // ISO → MM-DD
    portfolioChart: toChartNumber(point.totalNav as string),
    totalNav: point.totalNav,
  }));

  return (
    <section className="panel equity-panel">
      <div className="panel-header">
        <div>
          <h2>资产净值走势</h2>
          <p>近 {points.length || 30} 个交易日 · 组合总资产（¥）</p>
        </div>
        <div className="chart-legend">
          <span>
            <i className="legend-teal" />
            我的资产
          </span>
        </div>
      </div>
      {loading ? (
        <div className="chart-container chart-empty">加载中…</div>
      ) : chartData.length === 0 ? (
        <div className="chart-container chart-empty">暂无净值历史数据</div>
      ) : (
        <div className="chart-container" aria-label="资产净值走势折线图">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={chartData}
              margin={{ top: 14, right: 12, bottom: 0, left: 5 }}
            >
              <defs>
                <linearGradient id="portfolioFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#0d9488" stopOpacity={0.18} />
                  <stop offset="100%" stopColor="#0d9488" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid
                stroke="#e2e8f0"
                strokeDasharray="3 4"
                vertical={false}
              />
              <XAxis
                dataKey="date"
                axisLine={false}
                tickLine={false}
                tick={{ fontSize: 9, fill: "#94a3b8" }}
                interval={Math.ceil(chartData.length / 8)}
              />
              <YAxis domain={["dataMin - 1", "dataMax + 1"]} hide />
              <Tooltip
                formatter={(value, _name, item) => [
                  `¥${Number(item?.payload?.totalNav ?? value).toLocaleString("zh-CN", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}`,
                  "总资产",
                ]}
                contentStyle={{
                  border: "1px solid #e2e8f0",
                  borderRadius: 8,
                  fontSize: 10,
                }}
              />
              <Area
                isAnimationActive={false}
                type="monotone"
                dataKey="portfolioChart"
                stroke="#0d9488"
                strokeWidth={2}
                fill="url(#portfolioFill)"
                dot={false}
                activeDot={{ r: 3 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

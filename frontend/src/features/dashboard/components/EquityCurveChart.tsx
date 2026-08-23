import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  normalizeChartIndex,
  toChartNumber,
} from "../../../domain/chart-number-adapter";
import { equityCurve } from "../dashboard-data";

const chartData = equityCurve.map((point) => ({
  ...point,
  portfolioChart: normalizeChartIndex(
    point.portfolio,
    equityCurve[0].portfolio,
  ),
  benchmarkChart: toChartNumber(point.benchmark),
}));

export default function EquityCurveChart() {
  return (
    <section className="panel equity-panel">
      <div className="panel-header">
        <div>
          <h2>资产净值走势</h2>
          <p>近 30 日 · 已归一化展示基准相对走势</p>
        </div>
        <div className="chart-legend">
          <span>
            <i className="legend-teal" />
            我的资产
          </span>
          <span>
            <i className="legend-slate" />
            沪深300
          </span>
        </div>
      </div>
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
              interval={2}
            />
            <YAxis domain={["dataMin - 1", "dataMax + 1"]} hide />
            <Tooltip
              formatter={(value, name) => [
                `${Number(value).toFixed(2)}`,
                name === "portfolioChart" ? "我的资产" : "沪深300",
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
            <Line
              isAnimationActive={false}
              type="monotone"
              dataKey="benchmarkChart"
              stroke="#94a3b8"
              strokeWidth={1.5}
              strokeDasharray="4 4"
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

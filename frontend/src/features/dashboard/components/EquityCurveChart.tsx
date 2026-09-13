import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartNoAxesCombined } from "lucide-react";
import { toChartNumber } from "../../../domain/chart-number-adapter";
import type { NavPoint } from "../../../api/types";
import EmptyState from "../../../components/data-state/EmptyState";

export default function EquityCurveChart({
  points,
  loading,
}: {
  points: NavPoint[];
  loading?: boolean;
}) {
  const chartData = points
    .filter((point) => point.totalNav !== null)
    .map((point) => ({
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
        <div className="chart-container dashboard-data-loading">加载中…</div>
      ) : chartData.length === 0 ? (
        <EmptyState
          className="dashboard-panel-empty dashboard-panel-empty-chart"
          title="暂无净值记录"
          detail="导入或录入持仓后，将从首个有效快照开始绘制趋势。"
          icon={ChartNoAxesCombined}
        />
      ) : (
        <div className="chart-container" aria-label="资产净值走势折线图">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={chartData}
              margin={{ top: 14, right: 12, bottom: 0, left: 5 }}
            >
              <defs>
                <linearGradient id="portfolioFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#5ac8fa" stopOpacity={0.18} />
                  <stop offset="100%" stopColor="#5ac8fa" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid
                stroke="rgba(255, 255, 255, 0.15)"
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
                  `¥${Number(item?.payload?.totalNav ?? value).toLocaleString(
                    "zh-CN",
                    {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    },
                  )}`,
                  "总资产",
                ]}
                contentStyle={{
                  border: "1px solid rgba(255, 255, 255, 0.15)",
                  borderRadius: 8,
                  fontSize: 10,
                }}
              />
              <Area
                isAnimationActive={false}
                type="monotone"
                dataKey="portfolioChart"
                stroke="#5ac8fa"
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

import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { ChartPie } from "lucide-react";
import { toChartNumber } from "../../../domain/chart-number-adapter";
import { formatMoney } from "../../../domain/money";
import type { AllocationItem } from "../../../api/types";
import EmptyState from "../../../components/data-state/EmptyState";

const CATEGORY_COLOR: Record<string, string> = {
  A股: "#0d9488",
  场内ETF: "#0ea5e9",
  场外基金: "#8b5cf6",
  现金: "#94a3b8",
  债券: "#f59e0b",
  黄金: "#eab308",
};

export default function AssetDonutChart({
  allocation,
  loading,
}: {
  allocation: AllocationItem[];
  loading?: boolean;
}) {
  const chartData = allocation
    .filter((item) => item.value !== null)
    .map((item) => ({
      name: item.category,
      value: toChartNumber(item.value as string),
      color: CATEGORY_COLOR[item.category] ?? "#94a3b8",
      amount: item.amount,
    }));

  return (
    <section className="panel allocation-panel">
      <div className="panel-header">
        <div>
          <h2>资产配置</h2>
          <p>按最新可用估值口径</p>
        </div>
      </div>
      {loading ? (
        <div className="allocation-content dashboard-data-loading">加载中…</div>
      ) : chartData.length === 0 ? (
        <EmptyState
          className="dashboard-panel-empty dashboard-panel-empty-allocation"
          title="暂无资产配置"
          detail="持仓建立后，将按资产类型展示占比。"
          icon={ChartPie}
        />
      ) : (
        <div className="allocation-content">
          <div className="donut-wrap" aria-label="资产配置环形图">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  isAnimationActive={false}
                  data={chartData}
                  dataKey="value"
                  innerRadius="62%"
                  outerRadius="86%"
                  paddingAngle={2}
                  stroke="none"
                >
                  {chartData.map((item) => (
                    <Cell key={item.name} fill={item.color} />
                  ))}
                </Pie>
                <Tooltip
                  formatter={(value, _name, item) => [
                    `${value}% · ${formatMoney(String(item?.payload?.amount ?? 0))}`,
                    "占比",
                  ]}
                  contentStyle={{
                    border: "1px solid #e2e8f0",
                    borderRadius: 8,
                    fontSize: 10,
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
            <div className="donut-center">
              <strong className="mono">100%</strong>
              <span>总资产</span>
            </div>
          </div>
          <div className="allocation-legend">
            {chartData.map((item) => (
              <div key={item.name}>
                <span>
                  <i style={{ background: item.color }} />
                  {item.name}
                </span>
                <strong className="mono">{item.value}%</strong>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

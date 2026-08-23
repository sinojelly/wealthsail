import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { toChartNumber } from "../../../domain/chart-number-adapter";
import { allocation } from "../dashboard-data";

const chartData = allocation.map((item) => ({
  ...item,
  chartValue: toChartNumber(item.value),
}));

export default function AssetDonutChart() {
  return (
    <section className="panel allocation-panel">
      <div className="panel-header">
        <div>
          <h2>资产配置</h2>
          <p>按最新可用估值口径</p>
        </div>
      </div>
      <div className="allocation-content">
        <div className="donut-wrap" aria-label="资产配置环形图">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                isAnimationActive={false}
                data={chartData}
                dataKey="chartValue"
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
                formatter={(value) => [`${value}%`, "占比"]}
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
          {allocation.map((item) => (
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
    </section>
  );
}

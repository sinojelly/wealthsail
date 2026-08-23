import {
  Banknote,
  Landmark,
  LineChart,
  TrendingUp,
  WalletCards,
} from "lucide-react";

const metrics = [
  {
    label: "总资产",
    value: "¥1,921,840",
    hint: "16:00 估值快照",
    icon: Landmark,
    tone: "teal",
  },
  {
    label: "可用现金",
    value: "¥201,382",
    hint: "占总资产 10.5%",
    icon: Banknote,
    tone: "blue",
  },
  {
    label: "持仓市值",
    value: "¥1,720,458",
    hint: "股票 + 场内/场外基金",
    icon: WalletCards,
    tone: "purple",
  },
  {
    label: "当日收益",
    value: "+¥12,384",
    hint: "+0.65% · 沪深300 +0.39%",
    icon: TrendingUp,
    tone: "green",
    positive: true,
  },
  {
    label: "累计收益",
    value: "+¥152,800",
    hint: "+19.4% TWR · 超基准 +10.7%",
    icon: LineChart,
    tone: "amber",
    positive: true,
  },
];

export default function MetricCards() {
  return (
    <section className="metric-grid" aria-label="资产指标">
      {metrics.map(({ label, value, hint, icon: Icon, tone, positive }) => (
        <article className="metric-card panel" key={label}>
          <div className={`metric-icon tone-${tone}`}>
            <Icon size={17} />
          </div>
          <div className="metric-label">{label}</div>
          <strong className={`metric-value mono ${positive ? "positive" : ""}`}>
            {value}
          </strong>
          <span className="metric-hint">{hint}</span>
        </article>
      ))}
    </section>
  );
}

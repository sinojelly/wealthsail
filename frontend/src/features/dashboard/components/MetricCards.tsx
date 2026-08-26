import {
  Banknote,
  Landmark,
  LineChart,
  TrendingUp,
  WalletCards,
} from "lucide-react";
import Decimal from "decimal.js";
import { formatMoney, formatSignedMoney } from "../../../domain/money";
import type { PortfolioSummary } from "../../../api/types";

export default function MetricCards({
  summary,
  loading,
}: {
  summary?: PortfolioSummary;
  loading?: boolean;
}) {
  const total = summary?.totalAsset;
  const cashPct = summary?.totalAsset && !new Decimal(summary.totalAsset).isZero()
    ? new Decimal(summary.cashBalance)
        .div(summary.totalAsset)
        .mul(100)
        .toFixed(1)
    : null;

  const metrics = [
    {
      label: "总资产",
      value: total ? formatMoney(total) : "—",
      hint: loading ? "加载中…" : "最新估值快照",
      icon: Landmark,
      tone: "teal",
    },
    {
      label: "可用现金",
      value: summary?.cashBalance ? formatMoney(summary.cashBalance) : "—",
      hint: cashPct ? `占总资产 ${cashPct}%` : "—",
      icon: Banknote,
      tone: "blue",
    },
    {
      label: "持仓市值",
      value: summary?.positionsMarketValue
        ? formatMoney(summary.positionsMarketValue)
        : "—",
      hint: `${summary?.positionCount ?? 0} 个持仓标的`,
      icon: WalletCards,
      tone: "purple",
    },
    {
      label: "当日收益",
      value: summary?.dailyPnl ? formatSignedMoney(summary.dailyPnl) : "—",
      hint: summary?.dailyPnlRate ? `${summary.dailyPnlRate}%` : "—",
      icon: TrendingUp,
      tone: "green",
      positive: summary?.dailyPnl ? !summary.dailyPnl.startsWith("-") : false,
    },
    {
      label: "累计收益",
      value: summary?.cumulativePnl
        ? formatSignedMoney(summary.cumulativePnl)
        : "—",
      hint: summary?.cumulativePnlRate
        ? `${summary.cumulativePnlRate}% TWR`
        : "—",
      icon: LineChart,
      tone: "amber",
      positive: summary?.cumulativePnl
        ? !summary.cumulativePnl.startsWith("-")
        : false,
    },
  ];

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

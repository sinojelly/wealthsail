import {
  ArrowDownRight,
  ArrowUpRight,
  Gauge,
  Minus,
} from "lucide-react";
import type { MarketSentiment } from "../../../api/market";
import Decimal from "decimal.js";

function scoreTone(score: number | null): { label: string; cls: string } {
  if (score == null) return { label: "暂无评分", cls: "status-pill pending" };
  if (score >= 60) return { label: "情绪乐观", cls: "status-pill active" };
  if (score >= 40) return { label: "情绪中性", cls: "status-pill pending" };
  return { label: "情绪谨慎", cls: "status-pill danger" };
}

export default function SentimentCard({
  sentiment,
  loading,
}: {
  sentiment?: MarketSentiment;
  loading?: boolean;
}) {
  const score = sentiment?.sentimentScore
    ? Number(sentiment.sentimentScore)
    : null;
  const tone = scoreTone(score);
  const turnover = sentiment?.turnover ?? null;
  const north = sentiment?.northFlow ?? null;
  const updatedAt = sentiment?.updatedAt
    ? new Date(sentiment.updatedAt).toLocaleString("zh-CN", { hour12: false })
    : "--";

  const metrics: Array<{
    label: string;
    value: string | number | null;
    render: (v: string | number | null) => React.ReactNode;
  }> = [
    {
      label: "上涨家数",
      value: sentiment?.totalUp ?? null,
      render: (v) => (
        <span className="trend-up">
          {v == null ? "--" : String(v)} <ArrowUpRight size={13} />
        </span>
      ),
    },
    {
      label: "下跌家数",
      value: sentiment?.totalDown ?? null,
      render: (v) => (
        <span className="trend-down">
          {v == null ? "--" : String(v)} <ArrowDownRight size={13} />
        </span>
      ),
    },
    {
      label: "平盘",
      value: sentiment?.totalFlat ?? null,
      render: (v) => (
        <span className="text-muted">
          {v == null ? "--" : String(v)} <Minus size={13} />
        </span>
      ),
    },
    {
      label: "涨停 / 跌停",
      value: sentiment?.limitUp ?? null,
      render: () => (
        <span>
          <span className="trend-up">{sentiment?.limitUp ?? "--"}</span>
          {" / "}
          <span className="trend-down">{sentiment?.limitDown ?? "--"}</span>
        </span>
      ),
    },
    {
      label: "全市场成交额",
      value: turnover,
      render: (v) => (
        <span>{v == null ? "--" : `${new Decimal(String(v)).toFixed(0)} 亿`}</span>
      ),
    },
    {
      label: "北向净流入",
      value: north,
      render: (v) =>
        v == null ? (
          <span className="text-muted">暂不披露</span>
        ) : (
          <span className={new Decimal(String(v)).gte(0) ? "trend-up" : "trend-down"}>
            {new Decimal(String(v)).toFixed(2)} 亿
          </span>
        ),
    },
  ];

  return (
    <section className="panel sentiment-card">
      <div className="panel-header">
        <div>
          <h2>市场情绪指数</h2>
          <p>全市场涨跌结构与成交热度 · 数据源 AkShare/新浪</p>
        </div>
        <Gauge size={17} />
      </div>
      <div className="panel-body">
        <div className="sentiment-score-row">
          <div className="sentiment-score">
            <span className="score-value">
              {loading ? "…" : score == null ? "--" : score.toFixed(1)}
            </span>
            <span className={tone.cls}>{tone.label}</span>
          </div>
          <div className="sentiment-meta">
            <span>
              交易日期{" "}
              <strong className="mono">{sentiment?.tradeDate ?? "--"}</strong>
            </span>
            <span>
              更新于 <strong className="mono">{updatedAt}</strong>
            </span>
            <span className="text-muted">
              来源：{sentiment?.source === "live" ? "实时" : sentiment?.source === "db" ? "库内最近" : "不可用"}
            </span>
          </div>
        </div>
        <div className="metric-grid sentiment-grid">
          {metrics.map((m) => (
            <div className="metric-card" key={m.label}>
              <div className="metric-label">{m.label}</div>
              <div className="metric-value">{m.render(m.value)}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

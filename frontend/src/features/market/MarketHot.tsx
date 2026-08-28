import { useQuery } from "@tanstack/react-query";
import { Flame, RefreshCw } from "lucide-react";
import {
  fmtTurnoverYuan,
  fmtVolumeHands,
  getMarketHot,
  type MarketHotResponse,
} from "../../api/market";
import PageHeader from "../../components/layout/PageHeader";

type HotQuality = MarketHotResponse["qualityStatus"];

const QUALITY_PRESENTATION: Record<
  HotQuality,
  { label: string; detail: string; className: string }
> = {
  FRESH: {
    label: "新鲜快照",
    detail: "Worker 已刷新全市场热点快照，可按当前快照查看。",
    className: "alert-info",
  },
  ESTIMATED: {
    label: "估算热点",
    detail: "当前结果来自有限候选池，不代表全市场实时排名。",
    className: "alert-warning",
  },
  STALE: {
    label: "快照已过期",
    detail: "当前展示最近一次持久化结果，请勿视为实时行情。",
    className: "alert-warning",
  },
  UNAVAILABLE: {
    label: "热点数据不可用",
    detail: "暂无可审计的市场热点快照，请稍后刷新。",
    className: "alert-danger",
  },
};

/** 触发全局对话窗口查询某标的（ChatDrawer 监听同名事件）。 */
function askChatAboutStock(symbol: string, name: string) {
  window.dispatchEvent(
    new CustomEvent("wealthsail:chat-ask", {
      detail: {
        message: `帮我分析一下${name}（${symbol}）的当前可用行情快照与风险`,
      },
    }),
  );
}

function trendClass(changePct: number | null): string {
  if (changePct == null || changePct === 0) return "";
  // A 股习惯：红涨绿跌
  return changePct > 0 ? "trend-up" : "trend-down";
}

function formatPrice(price: number | null): string {
  return price == null ? "--" : price.toFixed(2);
}

function formatTimestamp(value: string | null): string {
  if (!value) return "--";
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return value;
  return timestamp.toLocaleString("zh-CN", { hour12: false });
}

export default function MarketHot() {
  const query = useQuery({
    queryKey: ["market", "hot"],
    queryFn: getMarketHot,
    refetchInterval: 60_000, // 每分钟轮询一次 Worker 持久化快照
  });

  const items = query.data?.items ?? [];
  const quality = query.data
    ? QUALITY_PRESENTATION[query.data.qualityStatus]
    : null;
  const updatedAt = formatTimestamp(query.data?.updatedAt ?? null);

  return (
    <>
      <PageHeader
        title="市场热点"
        description={`Worker 持久化热点快照前 10 · 快照更新 ${updatedAt}`}
        actions={
          <button
            type="button"
            className="btn-primary btn-sm"
            onClick={() => void query.refetch()}
            disabled={query.isFetching}
          >
            <RefreshCw size={14} className={query.isFetching ? "spin" : ""} />
            {query.isFetching ? "刷新中…" : "刷新"}
          </button>
        }
      />

      <div className="panel">
        {query.isError ? (
          <div className="alert alert-danger">
            <strong>行情获取失败</strong>
            <p>暂时无法读取市场热点快照，请稍后重试。</p>
          </div>
        ) : null}

        {query.data && quality ? (
          <div
            className={`alert ${quality.className}`}
            role={query.data.qualityStatus === "FRESH" ? "status" : "alert"}
          >
            <div>
              <strong>{quality.label}</strong>
              <p>{quality.detail}</p>
              <p className="mono">
                来源：{query.data.source || "--"} · 源时间：
                {formatTimestamp(query.data.sourceAsOf)} · 抓取时间：
                {formatTimestamp(query.data.retrievedAt)} · 方法：
                {query.data.methodVersion ?? "--"}
              </p>
            </div>
          </div>
        ) : null}

        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>排名</th>
                <th>代码</th>
                <th>名称</th>
                <th className="num">最新价</th>
                <th className="num">涨跌幅</th>
                <th className="num">成交量</th>
                <th className="num">成交额</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && !query.isFetching ? (
                <tr>
                  <td colSpan={7} className="muted">
                    暂无行情数据
                  </td>
                </tr>
              ) : (
                items.map((item, index) => (
                  <tr
                    key={item.symbol}
                    className="hot-row"
                    onClick={() => askChatAboutStock(item.symbol, item.name)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        askChatAboutStock(item.symbol, item.name);
                      }
                    }}
                    tabIndex={0}
                    role="button"
                    aria-label={`询问 AI：${item.name}（${item.symbol}）`}
                    title={`点击询问 AI：${item.name}（${item.symbol}）`}
                  >
                    <td>{index + 1}</td>
                    <td className="mono">{item.symbol}</td>
                    <td>{item.name}</td>
                    <td className="num">{formatPrice(item.price)}</td>
                    <td className={`num ${trendClass(item.change_pct)}`}>
                      {item.change_pct == null
                        ? "--"
                        : `${item.change_pct >= 0 ? "+" : ""}${item.change_pct.toFixed(2)}%`}
                    </td>
                    <td className="num">{fmtVolumeHands(item.volume)}</td>
                    <td className="num">{fmtTurnoverYuan(item.turnover)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <div className="market-hot-footer">
          <Flame size={14} />
          <span>点击任意一行，AI 助手将基于当前快照分析该标的行情与风险</span>
        </div>
      </div>
    </>
  );
}

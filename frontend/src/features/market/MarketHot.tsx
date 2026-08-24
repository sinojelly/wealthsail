import { useQuery } from "@tanstack/react-query";
import { Flame, RefreshCw } from "lucide-react";
import PageHeader from "../../components/layout/PageHeader";
import { fmtTurnoverYuan, fmtVolumeHands, getMarketHot } from "../../api/market";

/** 触发全局对话窗口查询某标的（ChatDrawer 监听同名事件）。 */
export function askChatAboutStock(symbol: string, name: string) {
  window.dispatchEvent(
    new CustomEvent("wealthsail:chat-ask", {
      detail: { message: `帮我分析下 ${name}（${symbol}）的实时行情和风险` },
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

export default function MarketHot() {
  const query = useQuery({
    queryKey: ["market", "hot"],
    queryFn: getMarketHot,
    refetchInterval: 60_000, // 实时更新：每分钟自动刷新
  });

  const items = query.data?.items ?? [];
  const updatedAt = query.data?.updatedAt
    ? new Date(query.data.updatedAt).toLocaleTimeString("zh-CN", { hour12: false })
    : "--";

  return (
    <>
      <PageHeader
        title="市场热点 · 实时更新"
        description={`全市场实时涨跌幅排行前 10 · 数据更新 ${updatedAt}`}
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
          <div className="alert alert-error">
            <strong>行情获取失败</strong>
            <p>数据源暂时不可用，请稍后重试。</p>
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
                    title={`点击询问 AI：${item.name}（${item.symbol}）`}
                  >
                    <td>{index + 1}</td>
                    <td className="mono">{item.symbol}</td>
                    <td>{item.name}</td>
                    <td className="num">{formatPrice(item.price)}</td>
                    <td className={`num ${trendClass(item.change_pct)}`}>
                      {item.change_pct == null ? "--" : `${item.change_pct >= 0 ? "+" : ""}${item.change_pct.toFixed(2)}%`}
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
          <span>点击任意一行，AI 助手将为你分析该标的的实时行情与风险</span>
        </div>
      </div>
    </>
  );
}

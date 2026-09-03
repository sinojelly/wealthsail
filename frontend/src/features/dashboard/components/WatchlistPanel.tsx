import { Loader2, Plus, Search, StarOff } from "lucide-react";
import { useEffect, useState } from "react";
import {
  useAddWatchlist,
  useInstrumentSearch,
  useRemoveWatchlist,
  useWatchlist,
} from "../../../api/hooks";
import type { InstrumentSearchResult } from "../../../api/types";

function formatAddedAt(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

export default function WatchlistPanel() {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [mutationError, setMutationError] = useState<string | null>(null);
  const watchlist = useWatchlist();
  const search = useInstrumentSearch(debouncedQuery);
  const addWatchlist = useAddWatchlist();
  const removeWatchlist = useRemoveWatchlist();
  const items = watchlist.data ?? [];
  const followedSymbols = new Set(items.map((item) => item.symbol));
  const addingSymbol = addWatchlist.isPending ? addWatchlist.variables?.symbol : undefined;
  const removingSymbol = removeWatchlist.isPending ? removeWatchlist.variables : undefined;

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  async function add(item: InstrumentSearchResult) {
    setMutationError(null);
    try {
      await addWatchlist.mutateAsync({
        symbol: item.symbol,
        // 把搜索结果元数据一起提交：基金等不在主数据时后端据此自动登记。
        name: item.name,
        assetType: item.type,
        market: item.market,
      });
      setQuery("");
      setDebouncedQuery("");
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "关注失败，请稍后重试");
    }
  }

  async function remove(symbol: string) {
    setMutationError(null);
    try {
      await removeWatchlist.mutateAsync(symbol);
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "取消关注失败，请稍后重试");
    }
  }

  const error = mutationError ?? (watchlist.error instanceof Error ? watchlist.error.message : null);

  return (
    <section className="watchlist-panel panel" aria-labelledby="watchlist-title">
      <div className="panel-header">
        <div>
          <h2 id="watchlist-title">我的关注</h2>
          <p>关注标的独立于持仓，不会修改资产账本。</p>
        </div>
      </div>
      <div className="panel-body">
        <label className="search-input watchlist-search" htmlFor="watchlist-search">
          <Search size={13} aria-hidden="true" />
          <input
            id="watchlist-search"
            aria-label="搜索关注标的"
            value={query}
            placeholder="代码或名称（至少 2 个字符）"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        {query.trim().length > 0 && query.trim().length < 2 ? (
          <p className="help-text">请至少输入 2 个字符后搜索</p>
        ) : null}
        {debouncedQuery.length >= 2 ? (
          <div className="watchlist-search-results" role="list" aria-label="关注标的搜索结果">
            {search.isLoading ? (
              <span className="search-result-hint"><Loader2 className="spin" size={13} /> 搜索中…</span>
            ) : search.error ? (
              <span className="search-result-hint">搜索服务暂不可用，请稍后重试</span>
            ) : (search.data ?? []).length === 0 ? (
              <span className="search-result-hint">未找到匹配标的</span>
            ) : (
              (search.data ?? []).map((item) => (
                <div className="watchlist-search-result" key={`${item.market}-${item.symbol}-${item.type}`}>
                  <span><strong>{item.name}</strong><small className="mono">{item.symbol} · {item.type}</small></span>
                  {followedSymbols.has(item.symbol) ? (
                    <span className="tag">已关注</span>
                  ) : (
                    <button type="button" className="btn-secondary" disabled={addingSymbol === item.symbol} onClick={() => void add(item)}>
                      {addingSymbol === item.symbol ? <Loader2 className="spin" size={13} /> : <Plus size={13} />}
                      关注 {item.name}
                    </button>
                  )}
                </div>
              ))
            )}
          </div>
        ) : null}
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        {watchlist.isLoading ? (
          <p className="watchlist-empty"><Loader2 className="spin" size={14} /> 正在加载关注列表…</p>
        ) : items.length === 0 ? (
          <p className="watchlist-empty">暂未关注标的。搜索并关注后，可在这里快速查看。</p>
        ) : (
          <ul className="watchlist-items">
            {items.map((item) => (
              <li key={item.symbol}>
                <div><strong>{item.assetName}</strong><span className="mono">{item.symbol} · {item.assetType}</span><small>关注于 {formatAddedAt(item.addedAt)}</small></div>
                <button type="button" className="btn-ghost" aria-label={`取消关注 ${item.assetName}`} disabled={removingSymbol === item.symbol} onClick={() => void remove(item.symbol)}>
                  {removingSymbol === item.symbol ? <Loader2 className="spin" size={13} /> : <StarOff size={13} />}
                  取消关注
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

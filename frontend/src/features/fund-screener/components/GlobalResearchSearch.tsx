import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { searchResearch, type SearchResult } from "../../../api/investment";
export default function GlobalResearchSearch({
  subject,
  onSelect,
}: {
  subject: string;
  onSelect: (item: SearchResult) => void;
}) {
  const [input, setInput] = useState("");
  const [keyword, setKeyword] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  useEffect(() => {
    const t = setTimeout(() => setKeyword(input.trim()), 500);
    return () => clearTimeout(t);
  }, [input]);
  const query = useQuery({
    queryKey: [subject, "research-search", keyword],
    queryFn: ({ signal }) => searchResearch(keyword, signal),
    enabled: !!keyword,
  });
  const results = input.trim() === keyword ? (query.data ?? []) : [];
  function select(item: SearchResult) {
    onSelect(item);
    setInput("");
    setKeyword("");
    setOpen(false);
  }
  return (
    <div className="op-search">
      <Search size={19} />
      <input
        role="combobox"
        aria-label="全局搜索"
        aria-expanded={open && !!input}
        aria-controls="research-results"
        aria-autocomplete="list"
        aria-activedescendant={
          open && results[active] ? `research-result-${active}` : undefined
        }
        value={input}
        placeholder="全局搜索指数、ETF、场外基金或代码"
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onChange={(e) => {
          setInput(e.target.value);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") setOpen(false);
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((n) => Math.min(n + 1, results.length - 1));
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((n) => Math.max(0, n - 1));
          }
          if (e.key === "Enter" && results[active]) select(results[active]);
        }}
      />
      {input && (
        <button
          aria-label="清除搜索"
          onClick={() => {
            setInput("");
            setKeyword("");
          }}
        >
          <X size={16} />
        </button>
      )}
      {open && input && (
        <div id="research-results" role="listbox" className="op-search-results">
          {query.isError ? (
            <p role="alert">搜索失败，请稍后重试。</p>
          ) : results.length ? (
            results.map((item, i) => (
              <button
                key={`${item.kind}:${item.id}`}
                id={`research-result-${i}`}
                role="option"
                aria-selected={active === i}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => select(item)}
              >
                <span>
                  <strong>{item.name}</strong>
                  <small>
                    {item.code} ·{" "}
                    {item.kind === "index"
                      ? "指数"
                      : item.tradeForm === "ETF"
                        ? "ETF"
                        : "场外基金"}
                  </small>
                </span>
                <span>查看 →</span>
              </button>
            ))
          ) : (
            <p>
              {query.isFetching || keyword !== input.trim()
                ? "搜索中…"
                : "没有匹配结果"}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

import { useState } from "react";
import { Check, Landmark } from "lucide-react";
import type { ResearchFund, ScreenResult } from "../../../api/investment";
export default function FundResultsTable({
  data,
  selectedCode,
  selectedId,
  selectedMarket,
  indexName,
  onSelect,
  page,
  onPage,
  loading,
  error,
}: {
  data?: ScreenResult;
  selectedCode: string | null;
  selectedId?: string | null;
  selectedMarket?: string;
  indexName?: string;
  onSelect: (fund: ResearchFund) => void;
  page: number;
  onPage: (page: number) => void;
  loading: boolean;
  error: boolean;
}) {
  const [more, setMore] = useState(false);
  const isSelected = (f: ResearchFund) =>
    selectedId
      ? f.instrumentId === selectedId
      : f.fundCode === selectedCode && f.market === selectedMarket;
  const labels: Record<string, string> = {
    READY: "已分析",
    NOT_COLLECTED: "待接入",
    RANGE_ONLY: "区间已采集",
    UNAVAILABLE: "来源不可用",
    UNVERIFIED: "来源待核验",
    STALE: "数据待更新",
    CONFLICT: "数据冲突",
    INSUFFICIENT: "历史不足",
    UNSUPPORTED: "不支持完整评分",
  };
  const technicalLabels: Record<string, string> = {
    STRONG: "趋势较强",
    MIXED: "信号不一致",
    WEAK: "趋势偏弱",
  };
  return (
    <section>
      <div className="op-section-label">
        <strong>{indexName ? `跟踪基金 · ${indexName}` : "基金候选"}</strong>
        <button aria-expanded={more} onClick={() => setMore(!more)}>
          {more ? "收起更多列" : "展开更多列"}⌄
        </button>
      </div>
      <div className="op-table-scroll">
        <table className="op-fund-table">
          <thead>
            <tr>
              <th>基金名称 / 代码</th>
              <th>类型</th>
              <th>成本</th>
              <th>跟踪质量</th>
              {more && (
                <>
                  <th>净值区间位置</th>
                  <th>最新净值</th>
                  <th>技术状态评分</th>
                </>
              )}
              <th>数据状态</th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((f) => (
              <tr
                key={`${f.market}:${f.fundCode}`}
                className={isSelected(f) ? "op-selected" : ""}
              >
                <td>
                  <button
                    aria-pressed={isSelected(f)}
                    onClick={() => onSelect(f)}
                  >
                    <span className="op-fund-icon">
                      {isSelected(f) ? (
                        <Check size={17} />
                      ) : (
                        <Landmark size={17} />
                      )}
                    </span>
                    <span>
                      <strong>{f.fundName}</strong>
                      <small>{f.fundCode}</small>
                    </span>
                  </button>
                </td>
                <td>{f.tradeForm === "ETF" ? "ETF" : "场外"}</td>
                <td>—</td>
                <td>—</td>
                {more && (
                  <>
                    <td>
                      {f.navRangePosition == null
                        ? "—"
                        : `${Number(f.navRangePosition).toFixed(2)}%`}
                    </td>
                    <td>
                      {f.latestValue == null
                        ? "—"
                        : Number(f.latestValue).toFixed(4)}
                      <small>{f.latestDate}</small>
                    </td>
                    <td>
                      {f.technicalScore == null
                        ? "—"
                        : `${Number(f.technicalScore).toFixed(0)} · ${technicalLabels[f.technicalState ?? ""] ?? "技术状态"}`}
                    </td>
                  </>
                )}
                <td>{labels[f.dataState] ?? "待核验"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {error ? (
        <p role="alert" className="op-empty">
          基金读取失败，请重试。
        </p>
      ) : loading ? (
        <p role="status" className="op-empty">
          基金加载中…
        </p>
      ) : (
        !data?.items.length && (
          <p className="op-empty">
            暂无符合条件的基金；关联资料未登记时，可直接搜索基金名称或代码。
          </p>
        )
      )}
      <footer className="op-pagination">
        <span>
          共 {data?.total ?? 0} 只 · 第 {page} 页
        </span>
        <div>
          <button
            aria-label="上一页"
            disabled={page <= 1 || loading}
            onClick={() => onPage(page - 1)}
          >
            ←
          </button>
          <button
            aria-label="下一页"
            disabled={loading || page * 20 >= (data?.total ?? 0)}
            onClick={() => onPage(page + 1)}
          >
            →
          </button>
        </div>
      </footer>
    </section>
  );
}

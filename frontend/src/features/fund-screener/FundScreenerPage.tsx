import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { RefreshCw, Search } from "lucide-react";
import PageHeader from "../../components/layout/PageHeader";
import {
  browseFunds,
  fmtDate,
  fmtDateSlash,
  fmtNav,
  fmtPercent,
  fmtPercentileLabel,
  fmtTimestamp,
  getFundRange,
  searchFunds,
  triggerFundFetch,
  type FundBrowseCategory,
  type FundNavRange,
  type FundRangeResponse,
} from "../../api/funds";

/** 抓取提交后的轮询间隔；Worker 完成写入缓存后下一次查询即拿到终态。 */
const FETCH_POLL_INTERVAL_MS = 5_000;
/** 单次抓取周期的兜底时限，超时后停止轮询并恢复按钮。 */
const FETCH_CYCLE_BACKSTOP_MS = 90_000;
/** 搜索结果含未收录基金时的刷新间隔与总窗口。 */
const SEARCH_POLL_INTERVAL_MS = 5_000;
const SEARCH_POLL_WINDOW_MS = 120_000;

/** 快速筛选入口（浏览缓存，不触发抓取）。 */
const QUICK_FILTERS: { key: FundBrowseCategory; label: string }[] = [
  { key: "stable", label: "稳健理财" },
  { key: "growth", label: "追求收益" },
  { key: "index", label: "指数投资" },
  { key: "overseas", label: "海外配置" },
  { key: "undervalued", label: "历史低估" },
  { key: "near_high", label: "接近高点" },
];

function isSixDigitCode(value: string): boolean {
  return /^[0-9]{6}$/.test(value);
}

/** 未收录行的加载占位：转圈 + 文案。 */
function FetchingCell() {
  return (
    <span className="fetching-cell">
      <RefreshCw className="spin" size={12} />
      正在获取…
    </span>
  );
}

/** 未收录行的骨架占位块。 */
function SkeletonCell() {
  return <span className="skeleton-block" aria-hidden="true" />;
}

/** 抓取中 / 刚提交失败时的公共提示。 */
function FetchFeedback({
  inFlight,
  failed,
}: {
  inFlight: boolean;
  failed: boolean;
}) {
  if (inFlight) {
    return (
      <p className="fund-fetch-hint" role="status">
        正在获取数据…通常几秒到几分钟内完成，页面会自动刷新结果。
      </p>
    );
  }
  if (failed) {
    return (
      <p className="fund-fetch-hint" role="alert">
        抓取请求提交失败，请稍后重试。
      </p>
    );
  }
  return null;
}

/** 详情区：未收录时自动抓取的提示（非卡片，纯文本行 + 操作按钮）。 */
function FundDetail({
  fundCode,
  detail,
  isLoading,
  isError,
  fetchInFlight,
  fetchFailed,
  onFetch,
}: {
  fundCode: string;
  detail: FundRangeResponse | null;
  isLoading: boolean;
  isError: boolean;
  fetchInFlight: boolean;
  fetchFailed: boolean;
  onFetch: (code: string) => void;
}) {
  if (isError) {
    return (
      <div className="fund-detail">
        <p className="fund-fetch-hint" role="alert">
          区间数据读取失败，请稍后重试。
        </p>
      </div>
    );
  }

  if (isLoading || detail == null) {
    return (
      <div className="fund-detail" role="status">
        <p className="fund-fetch-hint">正在加载数据…</p>
      </div>
    );
  }

  if (detail.status === "NOT_COLLECTED") {
    if (fetchFailed) {
      return (
        <div className="fund-detail">
          <p className="fund-fetch-hint" role="alert">
            抓取请求提交失败，请稍后重试。
          </p>
          <button
            type="button"
            className="btn-primary btn-sm"
            onClick={() => onFetch(fundCode)}
            disabled={fetchInFlight}
          >
            <RefreshCw size={14} />
            重试
          </button>
        </div>
      );
    }
    return (
      <div className="fund-detail" role="status">
        <p className="fund-fetch-hint">
          <RefreshCw className="spin" size={14} />
          正在获取 {fundCode} 的净值数据，通常几秒内完成…
        </p>
      </div>
    );
  }

  if (detail.status === "UNAVAILABLE") {
    return (
      <div className="fund-detail">
        <p className="search-result-hint" role="alert">
          <strong>{detail.fundName}</strong>（{detail.fundCode}）的数据源抓取失败，
          暂时无法获取，可稍后重试。
        </p>
        <FetchFeedback inFlight={fetchInFlight} failed={fetchFailed} />
        {!fetchFailed ? (
          <button
            type="button"
            className="btn-primary btn-sm"
            onClick={() => onFetch(detail.fundCode)}
            disabled={fetchInFlight}
          >
            <RefreshCw size={14} />
            {fetchInFlight ? "正在获取数据" : "重试获取"}
          </button>
        ) : null}
        <p className="fund-source-line mono">
          数据来源：{detail.dataSource} · 更新时间：
          {fmtTimestamp(detail.updatedAt)}
        </p>
      </div>
    );
  }

  return (
    <div className="fund-detail">
      <table className="data-table">
        <thead>
          <tr>
            <th>基金</th>
            <th className="num">最新净值</th>
            <th className="num">历史最高</th>
            <th className="num">历史最低</th>
            <th className="num">距最高回撤</th>
            <th className="num">距最低上涨</th>
            <th className="num">当前分位</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              {detail.fundName}
              <small className="cell-note mono">{detail.fundCode}</small>
            </td>
            <td className="num">
              {fmtNav(detail.latestNav)}
              <small className="cell-note">
                {fmtDate(detail.latestNavDate)}
              </small>
            </td>
            <td className="num">
              {fmtNav(detail.highestNav)}
              <small className="cell-note">
                {fmtDate(detail.highestNavDate)}
              </small>
            </td>
            <td className="num">
              {fmtNav(detail.lowestNav)}
              <small className="cell-note">
                {fmtDate(detail.lowestNavDate)}
              </small>
            </td>
            <td className="num fund-drawdown">
              {fmtPercent(detail.drawdownFromHigh)}
            </td>
            <td className="num fund-rise">
              {fmtPercent(detail.riseFromLow)}
            </td>
            <td className="num">
              {fmtPercentileLabel(detail.currentPercentile)}
            </td>
          </tr>
        </tbody>
      </table>
      <p className="fund-source-line">
        成立日期：{fmtDate(detail.inceptionDate)} · 数据来源：天天基金（AkShare）
        · 历史极值基于累计净值计算 · 更新于 {fmtDateSlash(detail.updatedAt)} ·
        不构成投资建议
      </p>
    </div>
  );
}

/** 搜索/快速筛选共用的净值区间表格。 */
function RangeTable({
  rows,
  activeCode,
  onSelect,
}: {
  rows: FundNavRange[];
  activeCode: string | null;
  onSelect: (code: string) => void;
}) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>基金</th>
          <th className="num">最新净值</th>
          <th className="num">历史最高</th>
          <th className="num">历史最低</th>
          <th className="num">距最高回撤</th>
          <th className="num">距最低上涨</th>
          <th className="num">当前分位</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((item) => {
          const isPending = item.status === "NOT_COLLECTED";
          return (
            <tr
              key={item.fundCode}
              className={`fund-row ${
                activeCode === item.fundCode ? "is-selected" : ""
              }`}
              onClick={() => onSelect(item.fundCode)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelect(item.fundCode);
                }
              }}
              tabIndex={0}
              role="button"
              aria-label={`查看 ${item.fundName}（${item.fundCode}）净值区间`}
            >
              <td>
                {item.fundName}
                <small className="cell-note mono">{item.fundCode}</small>
                {isPending ? (
                  <small className="cell-note">
                    净值数据抓取中，完成后自动填充…
                  </small>
                ) : null}
              </td>
              <td className="num">
                {isPending ? (
                  <FetchingCell />
                ) : (
                  <>
                    {fmtNav(item.latestNav)}
                    <small className="cell-note">
                      {fmtDate(item.latestNavDate)}
                    </small>
                  </>
                )}
              </td>
              <td className="num">
                {isPending ? (
                  <SkeletonCell />
                ) : (
                  <>
                    {fmtNav(item.highestNav)}
                    <small className="cell-note">
                      {fmtDate(item.highestNavDate)}
                    </small>
                  </>
                )}
              </td>
              <td className="num">
                {isPending ? (
                  <SkeletonCell />
                ) : (
                  <>
                    {fmtNav(item.lowestNav)}
                    <small className="cell-note">
                      {fmtDate(item.lowestNavDate)}
                    </small>
                  </>
                )}
              </td>
              <td className="num fund-drawdown">
                {isPending ? (
                  <SkeletonCell />
                ) : item.status === "UNAVAILABLE" ? (
                  "--"
                ) : (
                  fmtPercent(item.drawdownFromHigh)
                )}
              </td>
              <td className="num fund-rise">
                {isPending ? (
                  <SkeletonCell />
                ) : item.status === "UNAVAILABLE" ? (
                  "--"
                ) : (
                  fmtPercent(item.riseFromLow)
                )}
              </td>
              <td className="num">
                {isPending ? (
                  <SkeletonCell />
                ) : item.status === "UNAVAILABLE" ? (
                  "--"
                ) : (
                  fmtPercentileLabel(item.currentPercentile)
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

interface FetchCycle {
  code: string;
}

export default function FundScreenerPage() {
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [browseCategory, setBrowseCategory] =
    useState<FundBrowseCategory | null>(null);
  const [fetchCycle, setFetchCycle] = useState<FetchCycle | null>(null);
  const fetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 搜索结果存在未收录基金时的轮询截止时间（0 = 未在轮询）
  const searchPollDeadlineRef = useRef(0);

  // 输入防抖 500ms，避免逐字符打接口
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedKeyword(keyword.trim()), 500);
    return () => clearTimeout(timer);
  }, [keyword]);

  const searchQuery = useQuery({
    queryKey: ["funds", "search", debouncedKeyword],
    queryFn: () => searchFunds(debouncedKeyword),
    enabled: debouncedKeyword.length > 0,
    // 结果里还有"未收录"的基金时轮询：Worker 抓取完成后行数据自动填充
    refetchInterval: (query) => {
      const data = query.state.data;
      const hasPending =
        data?.some((item) => item.status === "NOT_COLLECTED") ?? false;
      if (!hasPending) {
        searchPollDeadlineRef.current = 0;
        return false;
      }
      const now = Date.now();
      if (searchPollDeadlineRef.current === 0) {
        searchPollDeadlineRef.current = now + SEARCH_POLL_WINDOW_MS;
      }
      if (now > searchPollDeadlineRef.current) return false;
      return SEARCH_POLL_INTERVAL_MS;
    },
  });

  const browseQuery = useQuery({
    queryKey: ["funds", "browse", browseCategory],
    queryFn: () => browseFunds(browseCategory as FundBrowseCategory),
    enabled: browseCategory != null,
  });

  // 输入恰好是 6 位代码但缓存无匹配时，自动进入该代码的抓取流程
  const directCode = isSixDigitCode(debouncedKeyword)
    ? debouncedKeyword
    : null;
  const searchActive = debouncedKeyword.length > 0;
  const autoLookup = Boolean(
    directCode && !searchQuery.isFetching && (searchQuery.data ?? []).length === 0,
  );
  // 名称搜索结果全部未收录时，自动选中第一只进入抓取流程
  const items: FundNavRange[] = searchQuery.data ?? [];
  const allNotCollected =
    items.length > 0 && items.every((item) => item.status === "NOT_COLLECTED");
  const autoSelectCode =
    allNotCollected && !searchQuery.isFetching ? items[0].fundCode : null;
  const activeCode =
    selectedCode ??
    (autoLookup ? directCode : null) ??
    (searchActive ? autoSelectCode : null);

  const detailQuery = useQuery({
    queryKey: ["funds", "range", activeCode],
    queryFn: () => getFundRange(activeCode as string),
    enabled: activeCode != null,
    // 抓取周期内轮询缓存；拿到终态（已收录/确认不可用）或超时后停止
    refetchInterval: (query) => {
      if (fetchCycle == null || fetchCycle.code !== activeCode) return false;
      const data = query.state.data;
      if (data && data.status === "AVAILABLE") return false;
      return FETCH_POLL_INTERVAL_MS;
    },
  });

  const fetchMutation = useMutation({
    mutationFn: triggerFundFetch,
    onSuccess: (data) => {
      if (fetchTimerRef.current) clearTimeout(fetchTimerRef.current);
      setFetchCycle({ code: data.fundCode });
      fetchTimerRef.current = setTimeout(() => {
        setFetchCycle(null);
      }, FETCH_CYCLE_BACKSTOP_MS);
    },
  });

  function selectFund(code: string) {
    setSelectedCode(code);
    if (fetchTimerRef.current) {
      clearTimeout(fetchTimerRef.current);
      fetchTimerRef.current = null;
    }
    setFetchCycle(null);
    // 清掉上一次点击残留的提交失败状态，避免错误提示泄漏到其他基金
    fetchMutation.reset();
  }

  function clearSelection() {
    setSelectedCode(null);
    if (fetchTimerRef.current) {
      clearTimeout(fetchTimerRef.current);
      fetchTimerRef.current = null;
    }
    setFetchCycle(null);
    fetchMutation.reset();
  }

  function toggleBrowse(key: FundBrowseCategory) {
    if (browseCategory === key) {
      setBrowseCategory(null);
      return;
    }
    setBrowseCategory(key);
    setKeyword("");
    setDebouncedKeyword("");
    clearSelection();
  }

  const browseActive = browseCategory != null && !searchActive;
  const browseItems: FundNavRange[] = browseQuery.data ?? [];
  const tableItems = searchActive ? items : browseItems;
  // 脚注的"更新于"取本次结果中最新的缓存更新日期
  const latestUpdated = fmtDateSlash(
    tableItems.reduce<string | null>(
      (latest, item) =>
        item.updatedAt && (!latest || item.updatedAt > latest)
          ? item.updatedAt
          : latest,
      null,
    ),
  );

  const detail: FundRangeResponse | null = detailQuery.data ?? null;
  const fetchFailed = fetchMutation.isError;
  // 抓取周期是否仍在进行：周期匹配当前基金，且尚未拿到可用数据
  const fetchInFlight =
    fetchCycle != null &&
    fetchCycle.code === activeCode &&
    (detail == null ||
      detail.status === "NOT_COLLECTED" ||
      detail.status === "UNAVAILABLE") &&
    !fetchFailed;

  // 未收录时自动提交抓取请求：后端 search 已幂等入队，这里补一次触发以进入轮询
  const { mutate: mutateFetch } = fetchMutation;
  const shouldAutoFetch =
    detail != null &&
    detail.status === "NOT_COLLECTED" &&
    fetchCycle == null &&
    !fetchFailed &&
    activeCode != null;
  useEffect(() => {
    if (shouldAutoFetch && activeCode != null) {
      mutateFetch(activeCode);
    }
  }, [shouldAutoFetch, activeCode, mutateFetch]);

  return (
    <>
      <PageHeader
        title="基金筛选"
        description="按代码或名称搜索基金，或用快速筛选浏览已收录基金的净值区间、回撤与分位"
      />

      <div className="panel">
        <div className="fund-search-row">
          <div className="fund-search-box">
            <Search size={15} />
            <input
              type="text"
              value={keyword}
              onChange={(event) => {
                setKeyword(event.target.value);
                clearSelection();
              }}
              placeholder="输入基金代码或名称，如 110022 / 沪深300"
              aria-label="搜索基金"
            />
            {searchQuery.isFetching ? (
              <span className="muted">搜索中…</span>
            ) : null}
          </div>
        </div>

        <div className="fund-chips" role="group" aria-label="快速筛选">
          {QUICK_FILTERS.map((filter) => (
            <button
              key={filter.key}
              type="button"
              className={`fund-chip ${
                browseCategory === filter.key && !searchActive
                  ? "is-active"
                  : ""
              }`}
              onClick={() => toggleBrowse(filter.key)}
            >
              {filter.label}
            </button>
          ))}
        </div>

        {searchQuery.isError ? (
          <div className="alert alert-danger" role="alert">
            <strong>搜索失败</strong>
            <p>暂时无法搜索基金，请稍后重试。</p>
          </div>
        ) : null}

        {searchActive && items.length > 0 ? (
          <div className="table-wrap">
            <RangeTable
              rows={items}
              activeCode={activeCode}
              onSelect={selectFund}
            />
          </div>
        ) : null}

        {searchActive &&
        !searchQuery.isFetching &&
        items.length === 0 &&
        !searchQuery.isError &&
        directCode == null ? (
          <div className="search-result-hint">
            未找到匹配的已收录基金，可尝试其他代码或名称。
          </div>
        ) : null}

        {browseActive && browseQuery.isError ? (
          <div className="alert alert-danger" role="alert">
            <strong>筛选失败</strong>
            <p>暂时无法获取筛选结果，请稍后重试。</p>
          </div>
        ) : null}

        {browseActive && browseQuery.isSuccess && browseItems.length > 0 ? (
          <div className="table-wrap">
            <RangeTable
              rows={browseItems}
              activeCode={activeCode}
              onSelect={selectFund}
            />
          </div>
        ) : null}

        {browseActive && browseQuery.isSuccess && browseItems.length === 0 ? (
          <div className="search-result-hint">
            暂无符合条件的基金——数据会随搜索与抓取逐步累积。
          </div>
        ) : null}

        {activeCode != null ? (
          <FundDetail
            fundCode={activeCode}
            detail={detail}
            isLoading={detailQuery.isLoading}
            isError={detailQuery.isError}
            fetchInFlight={fetchInFlight}
            fetchFailed={fetchFailed}
            onFetch={(code) => mutateFetch(code)}
          />
        ) : null}

        {tableItems.length > 0 && (searchActive || browseActive) ? (
          <p className="fund-source-line">
            数据来源：天天基金 · 历史极值基于累计净值计算 · 更新于 {latestUpdated}
          </p>
        ) : null}
      </div>
    </>
  );
}

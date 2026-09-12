import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { RefreshCw, Search } from "lucide-react";
import PageHeader from "../../components/layout/PageHeader";
import {
  fmtDate,
  fmtDateSlash,
  fmtNav,
  fmtPercent,
  fmtPercentileLabel,
  fmtTimestamp,
  getFundRange,
  searchFunds,
  triggerFundFetch,
  type FundNavRange,
  type FundRangeResponse,
} from "../../api/funds";

/** 抓取提交后的轮询间隔；Worker 完成写入缓存后下一次查询即拿到终态。 */
const FETCH_POLL_INTERVAL_MS = 5_000;
/** 单次抓取周期的兜底时限，超时后停止轮询并恢复按钮。 */
const FETCH_CYCLE_BACKSTOP_MS = 90_000;

function isSixDigitCode(value: string): boolean {
  return /^[0-9]{6}$/.test(value);
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
    return (
      <div className="fund-detail">
        <p className="search-result-hint">
          <strong>{fundCode}</strong>{" "}
          尚未收录，已自动提交按需抓取请求，通常几秒到几分钟内完成，完成后自动刷新。
        </p>
        <FetchFeedback inFlight={fetchInFlight} failed={fetchFailed} />
        {fetchFailed ? (
          <button
            type="button"
            className="btn-primary btn-sm"
            onClick={() => onFetch(fundCode)}
            disabled={fetchInFlight}
          >
            <RefreshCw size={14} />
            重试
          </button>
        ) : null}
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

interface FetchCycle {
  code: string;
}

export default function FundScreenerPage() {
  const [keyword, setKeyword] = useState("");
  const [debouncedKeyword, setDebouncedKeyword] = useState("");
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [fetchCycle, setFetchCycle] = useState<FetchCycle | null>(null);
  const fetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 输入防抖 500ms，避免逐字符打接口
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedKeyword(keyword.trim()), 500);
    return () => clearTimeout(timer);
  }, [keyword]);

  const searchQuery = useQuery({
    queryKey: ["funds", "search", debouncedKeyword],
    queryFn: () => searchFunds(debouncedKeyword),
    enabled: debouncedKeyword.length > 0,
  });

  // 输入恰好是 6 位代码但缓存无匹配时，自动进入该代码的抓取流程
  const directCode = isSixDigitCode(debouncedKeyword)
    ? debouncedKeyword
    : null;
  const autoLookup = Boolean(
    directCode && !searchQuery.isFetching && (searchQuery.data ?? []).length === 0,
  );
  const activeCode = selectedCode ?? (autoLookup ? directCode : null);

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

  const items: FundNavRange[] = searchQuery.data ?? [];
  // 脚注的"更新于"取本次结果中最新的缓存更新日期
  const latestUpdated = fmtDateSlash(
    items.reduce<string | null>(
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
        description="按代码或名称搜索基金，查看累计净值的历史区间、回撤与分位（未收录的 6 位代码会自动触发抓取）"
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

        {searchQuery.isError ? (
          <div className="alert alert-danger" role="alert">
            <strong>搜索失败</strong>
            <p>暂时无法搜索基金，请稍后重试。</p>
          </div>
        ) : null}

        {debouncedKeyword.length > 0 && items.length > 0 ? (
          <div className="table-wrap">
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
                {items.map((item) => (
                  <tr
                    key={item.fundCode}
                    className={`fund-row ${
                      activeCode === item.fundCode ? "is-selected" : ""
                    }`}
                    onClick={() => selectFund(item.fundCode)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        selectFund(item.fundCode);
                      }
                    }}
                    tabIndex={0}
                    role="button"
                    aria-label={`查看 ${item.fundName}（${item.fundCode}）净值区间`}
                  >
                    <td>
                      {item.fundName}
                      <small className="cell-note mono">{item.fundCode}</small>
                    </td>
                    <td className="num">
                      {fmtNav(item.latestNav)}
                      <small className="cell-note">
                        {fmtDate(item.latestNavDate)}
                      </small>
                    </td>
                    <td className="num">
                      {fmtNav(item.highestNav)}
                      <small className="cell-note">
                        {fmtDate(item.highestNavDate)}
                      </small>
                    </td>
                    <td className="num">
                      {fmtNav(item.lowestNav)}
                      <small className="cell-note">
                        {fmtDate(item.lowestNavDate)}
                      </small>
                    </td>
                    <td className="num fund-drawdown">
                      {item.status === "UNAVAILABLE"
                        ? "--"
                        : fmtPercent(item.drawdownFromHigh)}
                    </td>
                    <td className="num fund-rise">
                      {item.status === "UNAVAILABLE"
                        ? "--"
                        : fmtPercent(item.riseFromLow)}
                    </td>
                    <td className="num">
                      {item.status === "UNAVAILABLE"
                        ? "--"
                        : fmtPercentileLabel(item.currentPercentile)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {debouncedKeyword.length > 0 &&
        !searchQuery.isFetching &&
        items.length === 0 &&
        !searchQuery.isError &&
        directCode == null ? (
          <div className="search-result-hint">
            未找到匹配的已收录基金，可尝试其他代码或名称。
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

        {debouncedKeyword.length > 0 && items.length > 0 ? (
          <p className="fund-source-line">
            数据来源：天天基金 · 历史极值基于累计净值计算 · 更新于 {latestUpdated}
          </p>
        ) : null}
      </div>
    </>
  );
}

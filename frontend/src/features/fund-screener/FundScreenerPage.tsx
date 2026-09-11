import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, RefreshCw, Search } from "lucide-react";
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

function FundStat({
  label,
  value,
  valueClass,
  note,
}: {
  label: string;
  value: string;
  valueClass?: string;
  note?: string;
}) {
  return (
    <div className="fund-stat">
      <dt>{label}</dt>
      <dd className={valueClass}>{value}</dd>
      {note ? <small>{note}</small> : null}
    </div>
  );
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

/** 详情卡片：AVAILABLE / NOT_COLLECTED / UNAVAILABLE 三态。 */
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
      <div className="alert alert-danger" role="alert">
        <strong>区间数据读取失败</strong>
        <p>暂时无法读取该基金的净值区间，请稍后重试。</p>
      </div>
    );
  }

  if (isLoading || detail == null) {
    return (
      <div className="fund-detail" role="status">
        正在加载数据…
      </div>
    );
  }

  if (detail.status === "NOT_COLLECTED") {
    return (
      <div className="fund-detail">
        <div className="alert alert-warning">
          <strong>{fundCode} 未收录</strong>
          <p>
            该基金尚未进入净值区间缓存。点击下方按钮提交按需抓取，
            Worker 将从数据源获取累计净值并计算历史区间。
          </p>
          <button
            type="button"
            className="btn-primary btn-sm"
            onClick={() => onFetch(fundCode)}
            disabled={fetchInFlight}
          >
            <Download size={14} />
            {fetchInFlight ? "正在获取数据" : "获取数据"}
          </button>
          <FetchFeedback inFlight={fetchInFlight} failed={fetchFailed} />
        </div>
      </div>
    );
  }

  if (detail.status === "UNAVAILABLE") {
    return (
      <div className="fund-detail">
        <div className="alert alert-danger">
          <strong>暂时无法获取</strong>
          <p>
            {detail.fundName}（{detail.fundCode}）的数据源抓取失败，
            当前没有可展示的净值区间，稍后可重试。
          </p>
          <button
            type="button"
            className="btn-primary btn-sm"
            onClick={() => onFetch(fundCode)}
            disabled={fetchInFlight}
          >
            <RefreshCw size={14} />
            {fetchInFlight ? "正在获取数据" : "重试获取"}
          </button>
          <FetchFeedback inFlight={fetchInFlight} failed={fetchFailed} />
          <p className="mono">
            数据来源：{detail.dataSource} · 更新时间：
            {fmtTimestamp(detail.updatedAt)}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="fund-detail">
      <div className="fund-detail-head">
        <strong>{detail.fundName}</strong>
        <span className="mono">{detail.fundCode}</span>
        <span className="status-pill">{detail.fundType}</span>
      </div>
      <dl className="fund-stat-grid">
        <FundStat
          label="最新净值（累计）"
          value={fmtNav(detail.latestNav)}
          note={`净值日期 ${fmtDate(detail.latestNavDate)}`}
        />
        <FundStat
          label="历史最高"
          value={fmtNav(detail.highestNav)}
          note={`出现于 ${fmtDate(detail.highestNavDate)}`}
        />
        <FundStat
          label="历史最低"
          value={fmtNav(detail.lowestNav)}
          note={`出现于 ${fmtDate(detail.lowestNavDate)}`}
        />
        <FundStat
          label="距高点回撤"
          value={fmtPercent(detail.drawdownFromHigh)}
          valueClass="trend-down"
          note="当前净值相对历史最高"
        />
        <FundStat
          label="距低点涨幅"
          value={fmtPercent(detail.riseFromLow)}
          valueClass="trend-up"
          note="当前净值相对历史最低"
        />
        <FundStat
          label="当前分位"
          value={
            detail.currentPercentile == null
              ? "--"
              : fmtPercent(detail.currentPercentile)
          }
          note="处于历史净值区间位置"
        />
        <FundStat label="成立日期" value={fmtDate(detail.inceptionDate)} />
      </dl>
      <p className="fund-source-line mono">
        数据来源：AkShare · 更新时间：{fmtTimestamp(detail.updatedAt)} ·
        仅使用累计净值，不构成投资建议
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

  const detailQuery = useQuery({
    queryKey: ["funds", "range", selectedCode],
    queryFn: () => getFundRange(selectedCode as string),
    enabled: selectedCode != null,
    // 抓取周期内轮询缓存；拿到终态（已收录/确认不可用）或超时后停止
    refetchInterval: (query) => {
      if (fetchCycle == null || fetchCycle.code !== selectedCode) return false;
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
  // 输入恰好是 6 位代码但缓存无匹配时，允许直接查询该代码（走未收录流程）
  const directCode = isSixDigitCode(debouncedKeyword)
    ? debouncedKeyword
    : null;
  const showDirectLookup =
    directCode != null && !searchQuery.isFetching && items.length === 0;

  // 抓取周期是否仍在进行：周期匹配当前基金，且尚未拿到可用数据
  const detail = detailQuery.data ?? null;
  const fetchInFlight =
    fetchCycle != null &&
    fetchCycle.code === selectedCode &&
    (detail == null ||
      detail.status === "NOT_COLLECTED" ||
      detail.status === "UNAVAILABLE") &&
    !fetchMutation.isError;

  return (
    <>
      <PageHeader
        title="基金筛选"
        description="按代码或名称搜索基金，查看累计净值的历史区间、回撤与分位（仅缓存已收录基金）"
      />

      <div className="panel">
        <div className="fund-search-row">
          <div className="fund-search-box">
            <Search size={15} />
            <input
              type="text"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
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

        {showDirectLookup && directCode != null ? (
          <div className="search-result-hint">
            未找到与“{debouncedKeyword}”匹配的已收录基金。
            {directCode} 可能尚未收录，
            <button
              type="button"
              className="link-button"
              onClick={() => selectFund(directCode)}
            >
              直接查询 {directCode} 的净值区间
            </button>
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
                      selectedCode === item.fundCode ? "is-selected" : ""
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

        {debouncedKeyword.length > 0 && items.length > 0 ? (
          <p className="fund-source-line">
            数据来源：天天基金 · 历史极值基于累计净值计算 · 更新于 {latestUpdated}
          </p>
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

        {selectedCode != null ? (
          <FundDetail
            fundCode={selectedCode}
            detail={detail}
            isLoading={detailQuery.isLoading}
            isError={detailQuery.isError}
            fetchInFlight={fetchInFlight}
            fetchFailed={fetchMutation.isError}
            onFetch={(code) => fetchMutation.mutate(code)}
          />
        ) : null}
      </div>
    </>
  );
}

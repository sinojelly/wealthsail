import { useEffect, useState, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Sparkles, ListPlus } from "lucide-react";
import { useAuthSession, getSessionSubject } from "../../app/auth-session";
import * as api from "../../api/investment";
import * as watchlist from "../../api/watchlist";
import {
  chooseFund,
  chooseIndex,
  locateSearch,
  resolveFund,
  type Selection,
} from "./selection";
import MacroContextStrip from "./components/MacroContextStrip";
import DirectionExplorer from "./components/DirectionExplorer";
import GlobalResearchSearch from "./components/GlobalResearchSearch";
import IndexComparison from "./components/IndexComparison";
import FundResultsTable from "./components/FundResultsTable";
import ResearchDiagnosis from "./components/ResearchDiagnosis";
import "./opportunities.css";

export default function FundScreenerPage() {
  const subject = getSessionSubject(useAuthSession());
  return <ResearchWorkspace key={subject} subject={subject} />;
}

function ResearchWorkspace({ subject }: { subject: string }) {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [direct, setDirect] = useState<api.SearchResult | null>(null);
  const [run, setRun] = useState<{
    id: string;
    target: string;
    until: number;
    attempt: number;
  } | null>(null);
  const attemptRef = useRef(0);
  const [timedOut, setTimedOut] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const requestedMonths = Number(params.get("macroMonths"));
  const macroMonths = [12, 36, 60].includes(requestedMonths) ? requestedMonths : 36;
  const directions = useQuery({
    queryKey: [subject, "directions"],
    queryFn: ({ signal }) => api.getDirections(signal),
  });
  const context = useQuery({
    queryKey: [subject, "market-context", macroMonths],
    queryFn: ({ signal }) => api.getMarketContext(signal, macroMonths),
  });
  const unlinked = params.get("unlinked") === "1";
  const directionId = unlinked
    ? null
    : (params.get("direction") ?? directions.data?.[0]?.id ?? null);
  const direction = directions.data?.find((d) => d.id === directionId);
  const indices = useQuery({
    queryKey: [subject, "indices", directionId],
    queryFn: ({ signal }) => api.getIndices(directionId!, signal),
    enabled: !!directionId,
  });
  const indexId = unlinked
    ? null
    : (params.get("index") ?? indices.data?.[0]?.id ?? null);
  const index = indices.data?.find((i) => i.id === indexId);
  const fundCode = params.get("fund");
  const selection: Selection = {
    directionId,
    indexId,
    fundId: params.get("instrument"),
    fundMarket: params.get("fundMarket"),
    fundCode,
  };
  const page = Math.max(1, Number(params.get("page")) || 1);
  const filters: api.ScreenFilters = {
    directionId: directionId ?? undefined,
    indexId: indexId ?? undefined,
    page,
    pageSize: 20,
    keyword: params.get("keyword") ?? undefined,
    tradeForm: params.get("tradeForm") ?? undefined,
    sortBy: params.get("sortBy") ?? "name",
    sortOrder: params.get("sortOrder") ?? "asc",
    analysisAsOf: params.get("analysisAsOf") ?? undefined,
    scorableOnly: params.get("scorableOnly") ?? undefined,
    performanceOnly: params.get("performanceOnly") ?? undefined,
    valueCandidateOnly: params.get("valueCandidateOnly") ?? undefined,
    minHistoryDays: params.get("minHistoryDays") ?? undefined,
  };
  const funds = useQuery({
    queryKey: [subject, "research-funds", filters],
    queryFn: ({ signal }) => api.screenFunds(filters, signal),
    enabled: unlinked || indices.isSuccess,
  });
  const restore = useQuery({
    queryKey: [subject, "restore-fund", fundCode],
    queryFn: ({ signal }) => api.searchResearch(fundCode!, signal),
    enabled: !!fundCode,
  });
  const selectedFund = resolveFund<api.ResearchFund | api.SearchResult>(
    selection,
    [
      ...(direct ? [direct] : []),
      ...(funds.data?.items ?? []),
      ...(restore.data?.filter((f) => f.kind === "fund") ?? []),
    ],
  );
  const mode = fundCode ? "fund" : "index";
  const objectId =
    mode === "fund"
      ? (selectedFund?.instrumentId ?? params.get("instrument"))
      : indexId;
  const analysisMode = mode === "fund" && !objectId ? "nav" : mode;
  const analysisId = objectId ?? (mode === "fund" ? fundCode : null);
  const target = `${subject}:${analysisMode}:${analysisId}`;
  const diagnosis = useQuery({
    queryKey: [subject, "analysis", analysisMode, analysisId],
    queryFn: ({ signal }) => api.getAnalysis(analysisMode, analysisId!, signal),
    enabled: !!analysisId,
  });
  const watched = useQuery({
    queryKey: [subject, "watchlist"],
    queryFn: () => watchlist.list(),
  });
  const add = useMutation({
    mutationFn: (input: watchlist.AddWatchlistInput) => watchlist.add(input),
    onSuccess: () =>
      client.invalidateQueries({
        predicate: (q) => q.queryKey.includes("watchlist"),
      }),
  });
  const refresh = useMutation({
    mutationFn: (input: {
      kind: "index" | "fund" | "nav";
      id: string;
      target: string;
    }) => api.refreshResearch(input.kind, input.id),
    onSuccess: (data, input) => {
      setTimedOut(false);
      setRun({
        id: data.runId,
        target: input.target,
        attempt: ++attemptRef.current,
        until: Date.now() + 120000,
      });
    },
  });
  const activeRun = run?.target === target ? run : null;
  const status = useQuery({
    queryKey: [subject, "refresh-status", activeRun?.id, activeRun?.attempt],
    queryFn: ({ signal }) => api.getRefreshStatus(activeRun!.id, signal),
    enabled: !!activeRun && !timedOut,
    refetchInterval: (q) =>
      ["COMPLETED", "FAILED"].includes(q.state.data?.status ?? "")
        ? false
        : 5000,
  });
  const completedAttempt = useRef(0);
  useEffect(() => {
    if (!activeRun) return;
    const t = setTimeout(
      () => setTimedOut(true),
      Math.max(0, activeRun.until - Date.now()),
    );
    return () => clearTimeout(t);
  }, [activeRun]);
  useEffect(() => {
    if (status.data?.status === "COMPLETED" && activeRun &&
        completedAttempt.current !== activeRun.attempt) {
      completedAttempt.current = activeRun.attempt;
      void client.invalidateQueries({
        predicate: (q) => q.queryKey[0] === subject &&
          ["analysis", "research-funds", "restore-fund", "research-search"].some(
            (key) => q.queryKey.includes(key)),
      });
      setParams((current) => {
        const next = new URLSearchParams(current);
        next.delete("analysisAsOf");
        next.delete("page");
        return next;
      }, { replace: true });
    }
  }, [status.data?.status, activeRun, client, subject, setParams]);
  function updateSelection(next: Selection) {
    const p = new URLSearchParams(params);
    for (const [k, v] of [
      ["direction", next.directionId],
      ["index", next.indexId],
      ["instrument", next.fundId],
      ["fund", next.fundCode],
      ["fundMarket", next.fundMarket],
    ] as const) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    if (next.directionId) p.delete("unlinked");
    else p.set("unlinked", "1");
    p.delete("page");
    p.delete("analysisAsOf");
    setParams(p);
    setRun(null);
    setTimedOut(false);
    add.reset();
    refresh.reset();
  }
  function selectFund(f: api.ResearchFund) {
    setDirect(null);
    updateSelection(chooseFund(selection, f));
  }
  function searchSelect(item: api.SearchResult) {
    setDirect(item);
    updateSelection(
      locateSearch({
        ...item,
        id: item.kind === "fund" ? (item.instrumentId ?? "") : item.id,
      }),
    );
  }
  function filter(k: string, v: string) {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    p.delete("page");
    p.delete("analysisAsOf");
    setParams(p, { replace: true });
  }
  const title =
    mode === "fund"
      ? (selectedFund?.fundName ??
        ("name" in (selectedFund ?? {})
          ? (selectedFund as api.SearchResult).name
          : fundCode!))
      : (index?.name ?? "请选择指数");
  const path = [
    direction?.marketLabel ?? "未确认市场",
    direction?.label ?? "未确认方向",
    index?.name ?? (fundCode ? "未确认关联指数" : null),
    fundCode,
  ]
    .filter(Boolean)
    .join(" / ");
  const watching = watched.data?.some((f) => f.symbol === fundCode);
  const busy =
    !!activeRun &&
    !timedOut &&
    !["COMPLETED", "FAILED"].includes(status.data?.status ?? "");
  const message = refresh.isError
    ? "更新提交失败，请重试。"
    : status.isError
      ? "更新状态读取失败，请稍后重试。"
      : timedOut
        ? "后台仍在处理，可稍后查看研究结果。"
        : status.data?.status === "FAILED"
          ? "更新失败，保留上次数据，可重试。"
          : status.data?.status === "COMPLETED"
            ? "研究数据已更新。"
            : "";
  return (
    <div className="opportunities">
      <header className="op-page-header">
        <div>
          <h1 aria-label="投资机会中心">
            <Sparkles size={20} />
            投资机会中心 <span className="op-research-badge">研究模式</span>
          </h1>
          <p>从市场环境到指数与基金的连续研究工作台</p>
        </div>
        <span className="op-muted">● 可核验研究 · 缺失数据不生成结论</span>
      </header>
      <GlobalResearchSearch
        key={subject}
        subject={subject}
        onSelect={searchSelect}
      />
      <MacroContextStrip data={context.data} error={context.isError}
        months={macroMonths} onMonthsChange={(months) => {
          const next = new URLSearchParams(params);
          next.set("macroMonths", String(months));
          setParams(next);
        }} />
      {directions.isError && (
        <p role="alert">方向读取失败，仍可直接搜索基金。</p>
      )}
      <DirectionExplorer
        directions={directions.data ?? []}
        selected={direction}
        onSelect={(id) => {
          setDirect(null);
          updateSelection({
            directionId: id,
            indexId: null,
            fundId: null,
            fundCode: null,
          });
        }}
      />
      <div className="op-workspace">
        <section className="op-panel op-candidates">
          <header>
            <div>
              <h2>候选选择</h2>
              <p aria-label="研究路径">{path}</p>
            </div>
            <span>选择指数研究风险暴露，选择基金研究投资工具</span>
          </header>
          <IndexComparison
            items={indices.data ?? []}
            selectedId={indexId}
            onSelect={(id) => {
              setDirect(null);
              updateSelection(chooseIndex(selection, id));
            }}
            loading={indices.isLoading && !!directionId}
            error={indices.isError}
          />
          <div className="op-filter-toggle">
            <button
              aria-expanded={filtersOpen}
              onClick={() => setFiltersOpen(!filtersOpen)}
            >
              高级筛选
            </button>
            <span>服务端组合筛选 · 范围内候选</span>
          </div>
          {filtersOpen && (
            <div className="op-filters">
              <label>
                关键词
                <input
                  aria-label="基金列表关键词"
                  value={params.get("keyword") ?? ""}
                  onChange={(e) => filter("keyword", e.target.value)}
                />
              </label>
              <label>
                类型
                <select
                  value={params.get("tradeForm") ?? ""}
                  onChange={(e) => filter("tradeForm", e.target.value)}
                >
                  <option value="">全部</option>
                  <option value="ETF">ETF</option>
                  <option value="FUND">场外基金</option>
                </select>
              </label>
              <label>
                排序
                <select
                  value={params.get("sortBy") ?? "name"}
                  onChange={(e) => filter("sortBy", e.target.value)}
                >
                  <option value="name">名称</option>
                  <option value="code">代码</option>
                  <option value="navPosition">净值区间位置</option>
                  <option value="technicalScore">技术状态评分</option>
                </select>
              </label>
              <label>
                指标覆盖
                <select
                  value={params.get("scorableOnly") ?? "false"}
                  onChange={(e) => filter("scorableOnly", e.target.value)}
                >
                  <option value="false">全部候选</option>
                  <option value="true">可完整评分</option>
                </select>
              </label>
              <label>
                研究资格
                <select value={params.get("valueCandidateOnly") ?? "false"}
                  onChange={(e) => filter("valueCandidateOnly", e.target.value)}>
                  <option value="false">全部候选</option>
                  <option value="true">估值、成本与风险证据候选</option>
                </select>
              </label>
              <label>
                收益风险覆盖
                <select value={params.get("performanceOnly") ?? "false"}
                  onChange={(e) => filter("performanceOnly", e.target.value)}>
                  <option value="false">全部历史状态</option>
                  <option value="true">可比收益与风险数据齐备</option>
                </select>
              </label>
              <label>
                最少历史日数
                <input
                  type="number"
                  min="0"
                  max="10000"
                  value={params.get("minHistoryDays") ?? ""}
                  onChange={(e) => filter("minHistoryDays", e.target.value)}
                />
              </label>
              <label>
                顺序
                <select
                  value={params.get("sortOrder") ?? "asc"}
                  onChange={(e) => filter("sortOrder", e.target.value)}
                >
                  <option value="asc">升序</option>
                  <option value="desc">降序</option>
                </select>
              </label>
            </div>
          )}
          <FundResultsTable
            data={funds.data}
            selectedCode={fundCode}
            selectedId={selectedFund?.instrumentId}
            selectedMarket={selectedFund?.market}
            indexName={index?.name}
            onSelect={selectFund}
            page={page}
            onPage={(p) => {
              const next = new URLSearchParams(params);
              next.set("page", String(p));
              if (funds.data?.analysisAsOf)
                next.set("analysisAsOf", funds.data.analysisAsOf);
              setParams(next);
            }}
            loading={funds.isFetching}
            error={funds.isError}
          />
        </section>
        <ResearchDiagnosis
          key={target}
          title={title}
          code={mode === "fund" ? fundCode! : index?.code}
          mode={mode}
          path={
            direction
              ? `${direction.marketLabel} / ${direction.label}`
              : "直接搜索"
          }
          data={diagnosis.data}
          loading={diagnosis.isLoading && !!objectId}
          error={diagnosis.isError}
          onRefresh={() => {
            if (analysisId)
              refresh.mutate({ kind: analysisMode, id: analysisId, target });
          }}
          refreshing={busy || refresh.isPending}
          canRefresh={!!analysisId}
          message={message}
          watchButton={
            <button
              className="op-watch"
              aria-label={mode === "index" ? "关注指数" : "加入自选基金"}
              disabled={
                mode === "index" ||
                !selectedFund?.market ||
                add.isPending ||
                watching ||
                add.isSuccess
              }
              title={
                mode === "index" ? "指数关注服务尚未接入" : "加入现有基金自选"
              }
              onClick={() => {
                if (selectedFund?.market && fundCode)
                  add.mutate({
                    symbol: fundCode,
                    name: title,
                    assetType:
                      selectedFund.tradeForm === "ETF" ? "ETF" : "FUND",
                    market: selectedFund.market,
                    currency: selectedFund.currency,
                  });
              }}
            >
              <ListPlus size={16} />
              {mode === "index"
                ? "关注指数"
                : watching || add.isSuccess
                  ? "已加入自选"
                  : "加入自选基金"}
            </button>
          }
        />
      </div>
      {add.isError && <p role="alert">加入自选失败，请重试。</p>}
    </div>
  );
}

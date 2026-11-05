import { useEffect, useState, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Sparkles, ListPlus } from "lucide-react";
import { useAuthSession, getSessionSubject } from "../../app/auth-session";
import * as api from "../../api/investment";
import * as watchlist from "../../api/watchlist";
import type { CreateStudy } from "../../api/study-api";
import { createStudyTrack } from "../../api/study-api";
import {
  chooseFund,
  chooseIndex,
  locateSearch,
  resolveFund,
  type Selection,
} from "./selection";
import MacroContextStrip from "./components/MacroContextStrip";
import GlobalResearchSearch from "./components/GlobalResearchSearch";
import FundResultsTable from "./components/FundResultsTable";
import ActionablePlan from "./components/ActionablePlan";
import { metricValue, timeLabel } from "./macro-display";
import StudyTracking from "./components/StudyTracking";
import TrackedFundWatch from "./components/TrackedFundWatch";
import DailyResearchRefresh from "./components/DailyResearchRefresh";
import StudyCreateConfirmation from "./components/StudyCreateConfirmation";
import { useEconomicArchivePolling } from "./economic-archive-polling";
import "./opportunities.css";

export default function FundScreenerPage() {
  const session = useAuthSession();
  const subject = getSessionSubject(session);
  return <ResearchWorkspace key={subject} subject={subject} />;
}

function ResearchWorkspace({ subject }: { subject: string }) {
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [manualOpen, setManualOpen] = useState(() =>
    ["fund", "instrument", "index"].some((key) => params.has(key)),
  );
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
  const macroMonths = [12, 36, 60].includes(requestedMonths)
    ? requestedMonths
    : 36;
  const directions = useQuery({
    queryKey: [subject, "directions"],
    queryFn: ({ signal }) => api.getDirections(signal),
  });
  const marketArchiveInterval = useEconomicArchivePolling();
  const opportunityArchiveInterval = useEconomicArchivePolling();
  const context = useQuery({
    queryKey: [subject, "market-context", macroMonths],
    queryFn: ({ signal }) => api.getMarketContext(signal, macroMonths),
    refetchInterval: marketArchiveInterval,
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
  const opportunityScope: api.OpportunityScope = {
    directionId: directionId ?? undefined,
    indexId: indexId ?? undefined,
    instrumentId: selectedFund?.instrumentId ?? selection.fundId ?? undefined,
    fundCode: fundCode ?? undefined,
    fundMarket: selectedFund?.market ?? selection.fundMarket ?? undefined,
  };
  const opportunityKey = [
    subject,
    "opportunity-context",
    directionId,
    indexId,
    opportunityScope.instrumentId ?? null,
    fundCode,
    opportunityScope.fundMarket ?? null,
    macroMonths,
  ] as const;
  const studyScopeKey = JSON.stringify(opportunityKey.slice(0, -1));
  const [planConfirmation, setPlanConfirmation] = useState<{
    result: api.ActionableZoneResult;
    track: api.PlanTrack;
  } | null>(null);
  const [confirmStudyScope, setConfirmStudyScope] = useState<string | null>(
    null,
  );
  const [confirmationScope, setConfirmationScope] = useState(studyScopeKey);
  if (confirmationScope !== studyScopeKey) {
    setConfirmationScope(studyScopeKey);
    setConfirmStudyScope(null);
    setPlanConfirmation(null);
  }
  const activeStudyScope = useRef<string | null>(studyScopeKey);
  useEffect(() => {
    activeStudyScope.current = studyScopeKey;
    return () => {
      activeStudyScope.current = null;
    };
  }, [studyScopeKey]);
  const studyCreate = useMutation({
    mutationFn: ({ origin }: { origin: CreateStudy; scopeKey: string }) =>
      createStudyTrack(origin),
    onSuccess: (result, variables) => {
      client.setQueryData(
        ["study", subject, "detail", result.track.id, 0],
        result,
      );
      void client.invalidateQueries({ queryKey: ["study", subject, "list"] });
      if (activeStudyScope.current !== variables.scopeKey) return;
      setConfirmStudyScope(null);
      setParams((current) => {
        const next = new URLSearchParams(current);
        next.set("researchOpen", "1");
        next.set("researchTab", "tracking");
        next.set("studyTrack", result.track.id);
        next.delete("researchData");
        return next;
      });
      document
        .querySelector('[aria-label="今日投资观察"]')
        ?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    },
  });
  const studyCreatingCurrent =
    studyCreate.variables?.scopeKey === studyScopeKey;
  const studyCreateError =
    studyCreate.isError && studyCreatingCurrent
      ? studyCreate.error instanceof Error
        ? studyCreate.error.message
        : "研究保存失败，请点击加入跟踪重试。"
      : undefined;
  const opportunity = useQuery({
    queryKey: opportunityKey,
    queryFn: ({ signal }) =>
      api.getOpportunityContext(
        { ...opportunityScope, months: macroMonths },
        signal,
      ),
    refetchInterval: opportunityArchiveInterval,
    enabled:
      directions.isSuccess && (unlinked || !directionId || indices.isSuccess),
  });
  const radarData = opportunity.data ?? context.data;
  const mode = fundCode || selection.fundId ? "fund" : "index";
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
        queryKey: [subject, "watchlist"],
        exact: true,
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
    if (
      status.data?.status === "COMPLETED" &&
      activeRun &&
      completedAttempt.current !== activeRun.attempt
    ) {
      completedAttempt.current = activeRun.attempt;
      void client.invalidateQueries({
        predicate: (q) =>
          q.queryKey[0] === subject &&
          [
            "analysis",
            "research-funds",
            "restore-fund",
            "research-search",
            "opportunity-context",
            "market-context",
          ].some((key) => q.queryKey.includes(key)),
      });
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.delete("analysisAsOf");
          next.delete("page");
          return next;
        },
        { replace: true },
      );
    }
  }, [status.data?.status, activeRun, client, subject, setParams]);
  function updateSelection(next: Selection) {
    setManualOpen(true);
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
    p.delete("researchOpen");
    p.delete("researchTab");
    p.delete("researchData");
    p.delete("studyTrack");
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
          : (fundCode ?? selection.fundId ?? "请选择基金")))
      : (index?.name ?? "请选择指数");
  const path = [
    direction?.marketLabel ?? "未确认市场",
    direction?.label ?? "未确认方向",
    index?.name ?? (fundCode ? "未确认关联指数" : null),
    fundCode,
  ]
    .filter(Boolean)
    .join(" / ");
  const watchAssetType = selectedFund?.tradeForm === "ETF" ? "ETF" : "FUND";
  const watchedCode = watched.data?.some(
    (f) => f.symbol === fundCode && f.assetType === watchAssetType,
  );
  const watchActionCurrent =
    !!selectedFund?.market &&
    add.variables?.symbol === fundCode &&
    add.variables.market === selectedFund.market &&
    add.variables.assetType === watchAssetType;
  const watchAdded = add.isSuccess && watchActionCurrent;
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
  const currentZones =
    diagnosis.data?.actionableZones ??
    opportunity.data?.actionableZones?.find(
      (row) => row.subjectKind === analysisMode && row.subjectId === analysisId,
    );
  return (
    <div className="opportunities">
      <header className="op-page-header">
        <div>
          <h1 aria-label="投资机会中心">
            <Sparkles size={20} />
            投资机会中心 <span className="op-research-badge">研究模式</span>
          </h1>
          <p>每日综合宏观与要闻，发现价值方向和基金买卖区间</p>
        </div>
        <span className="op-muted">双轨机会 · 真实价格依据 · 跟踪复盘</span>
      </header>
      <MacroContextStrip
        data={radarData}
        renderTrackedFunds={() => <TrackedFundWatch actor={subject} />}
        renderResearchRefresh={() => <DailyResearchRefresh actor={subject} />}
        renderStudyTracking={() => (
          <StudyTracking
            actor={subject}
            scope={opportunityScope}
            preview={
              analysisId
                ? {
                    direction: direction?.label,
                    subject: title,
                    code: fundCode ?? index?.code ?? undefined,
                    plan: currentZones,
                  }
                : undefined
            }
          />
        )}
        onStudyDirection={async (input) => {
          const result = await createStudyTrack({
            originType: "DAILY_DIRECTION",
            ...input,
            horizonDays: 30,
          });
          client.setQueryData(
            ["study", subject, "detail", result.track.id, 0],
            result,
          );
          void client.invalidateQueries({
            queryKey: ["study", subject, "list"],
          });
        }}
        onStudyQuantScan={async (input) => {
          const result = await createStudyTrack({
            originType: "DAILY_QUANT_SCAN",
            ...input,
            horizonDays: 30,
          });
          client.setQueryData(
            ["study", subject, "detail", result.track.id, 0],
            result,
          );
          void client.invalidateQueries({
            queryKey: ["study", subject, "list"],
          });
        }}
        scopeKey={studyScopeKey}
        selectedDirection={direction}
        selectedIndexId={indexId ?? undefined}
        error={!radarData && (opportunity.isError || context.isError)}
        loading={!radarData && (opportunity.isPending || context.isPending)}
        directions={directions.data ?? []}
        onDirectionSelect={(id) => {
          setDirect(null);
          updateSelection({
            directionId: id,
            indexId: null,
            fundId: null,
            fundCode: null,
            fundMarket: null,
          });
        }}
        months={macroMonths}
        onMonthsChange={(months) => {
          const next = new URLSearchParams(params);
          next.set("macroMonths", String(months));
          setParams(next);
        }}
      />
      {directions.isError && (
        <p role="alert">方向读取失败，仍可直接搜索基金。</p>
      )}
      <details
        className="op-manual-research"
        open={manualOpen}
        onToggle={(event) => setManualOpen(event.currentTarget.open)}
      >
        <summary>手动研究指数与基金</summary>
        <GlobalResearchSearch
          key={subject}
          subject={subject}
          onSelect={searchSelect}
        />
        <div className="op-workspace">
          <section
            className="op-panel op-candidates"
            id="op-research-targets"
            tabIndex={-1}
          >
            <header>
              <div>
                <h2>候选选择</h2>
                <p aria-label="研究路径">{path}</p>
              </div>
              <span>选择指数研究风险暴露，选择基金研究投资工具</span>
            </header>
            <div className="op-workbench-selection">
              <label>
                市场与方向{" "}
                <select
                  aria-label="市场与方向"
                  value={directionId ?? ""}
                  onChange={(event) => {
                    setDirect(null);
                    updateSelection({
                      directionId: event.target.value || null,
                      indexId: null,
                      fundId: null,
                      fundCode: null,
                      fundMarket: null,
                    });
                  }}
                >
                  <option value="">全部基金</option>
                  {directions.data?.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.marketLabel} · {row.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                研究指数{" "}
                <select
                  aria-label="研究指数"
                  value={indexId ?? ""}
                  onChange={(event) => {
                    setDirect(null);
                    updateSelection(chooseIndex(selection, event.target.value));
                  }}
                  disabled={!directionId || indices.isPending}
                >
                  <option value="">请选择指数</option>
                  {indices.data?.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </select>
              </label>
              {indices.isError && (
                <p role="alert">指数读取失败，仍可直接搜索基金。</p>
              )}
            </div>
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
                  收益风险覆盖
                  <select
                    value={params.get("performanceOnly") ?? "false"}
                    onChange={(e) => filter("performanceOnly", e.target.value)}
                  >
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
          <aside
            className="op-panel op-target-research"
            aria-label="当前标的研究"
          >
            <header>
              <h2>{title}</h2>
              <p>
                {mode === "fund" ? (fundCode ?? selection.fundId) : index?.code}{" "}
                · {path}
              </p>
            </header>
            <button
              className="op-watch"
              aria-label={mode === "index" ? "关注指数" : "加入自选基金"}
              disabled={
                mode === "index" ||
                !selectedFund?.market ||
                (add.isPending && watchActionCurrent) ||
                watchedCode ||
                watchAdded
              }
              title={
                mode === "index"
                  ? "指数关注服务尚未接入"
                  : watchedCode && !watchAdded
                    ? "该代码和类型已有自选，现有服务未提供精确市场归属。"
                    : "加入现有基金自选"
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
                : watchAdded
                  ? "已加入自选"
                  : watchedCode
                    ? "代码已有自选 · 市场待核验"
                    : "加入自选基金"}
            </button>
            <button
              type="button"
              className="op-refresh"
              disabled={!analysisId || busy || refresh.isPending}
              onClick={() => {
                if (analysisId)
                  refresh.mutate({
                    kind: analysisMode,
                    id: analysisId,
                    target,
                  });
              }}
            >
              {busy || refresh.isPending ? "更新中…" : "更新研究数据"}
            </button>
            {message && <p role="status">{message}</p>}
            {diagnosis.isError && (
              <p role="alert">标的研究读取失败，请重试。</p>
            )}
            {diagnosis.isPending && analysisId && (
              <p role="status">标的研究加载中…</p>
            )}
            <p>
              研究截止：{timeLabel(diagnosis.data?.asOf)} · 数据来源：
              {typeof diagnosis.data?.source === "string"
                ? diagnosis.data.source
                : "待接入"}
            </p>
            {currentZones && (
              <ActionablePlan
                result={currentZones}
                pending={studyCreate.isPending && studyCreatingCurrent}
                onTrack={(track) => {
                  studyCreate.reset();
                  setPlanConfirmation({ result: currentZones, track });
                  setConfirmStudyScope(studyScopeKey);
                }}
              />
            )}
            {!currentZones && (
              <p>当前标的尚无可用双轨价格依据，仍可保存研究并跟踪。</p>
            )}
            <button
              type="button"
              disabled={
                !analysisId || (studyCreate.isPending && studyCreatingCurrent)
              }
              onClick={() => {
                studyCreate.reset();
                setPlanConfirmation(null);
                setConfirmStudyScope(studyScopeKey);
              }}
            >
              跟踪当前标的研究
            </button>
            {confirmStudyScope === studyScopeKey && (
              <StudyCreateConfirmation
                key={`study-confirm:${studyScopeKey}:${planConfirmation?.track ?? "research"}`}
                preview={{
                  title: `${title}研究`,
                  thesis:
                    opportunity.data?.directionModel?.summary ||
                    "保留当前观察，继续核对研究条件。",
                  watchFor:
                    opportunity.data?.directionModel?.missingEvidence ?? [],
                  counterFactors: (
                    opportunity.data?.directionModel?.counterFactors ?? []
                  ).map((row) => row.summary || row.label),
                  supports: (
                    opportunity.data?.directionModel?.supports ?? []
                  ).map((row) => row.summary || row.label),
                  sources: [],
                  selection: true,
                }}
                pending={studyCreate.isPending && studyCreatingCurrent}
                error={studyCreateError}
                onCancel={() => {
                  setConfirmStudyScope(null);
                  setPlanConfirmation(null);
                }}
                onSave={(horizonDays) =>
                  studyCreate.mutate({
                    origin: {
                      originType: "SELECTION",
                      scope: opportunityScope,
                      horizonDays,
                      ...(planConfirmation
                        ? { planTrack: planConfirmation.track }
                        : {}),
                    },
                    scopeKey: studyScopeKey,
                  })
                }
              >
                {planConfirmation && (
                  <ActionablePlan
                    result={{
                      ...planConfirmation.result,
                      selectedTrack: planConfirmation.track,
                      tracks: planConfirmation.result.tracks.filter(
                        (row) => row.track === planConfirmation.track,
                      ),
                    }}
                  />
                )}
              </StudyCreateConfirmation>
            )}
            {studyCreateError &&
              confirmStudyScope !== studyScopeKey &&
              studyCreate.variables?.origin.originType === "SELECTION" && (
                <p role="alert">{studyCreateError}</p>
              )}
            <details>
              <summary>历史收益与风险</summary>
              {diagnosis.data?.navChanges?.basis === "UNIT_NAV_CHANGE" &&
                diagnosis.data.navChanges.unit === "decimal_fraction" && (
                  <>
                    <p>
                      63期单位净值变动：
                      {metricValue(
                        diagnosis.data.navChanges.change63 == null
                          ? null
                          : diagnosis.data.navChanges.change63 * 100,
                      )}
                      % · 252期单位净值变动：
                      {metricValue(
                        diagnosis.data.navChanges.change252 == null
                          ? null
                          : diagnosis.data.navChanges.change252 * 100,
                      )}
                      %
                    </p>
                    <small>
                      单位净值变动不含分红再投，不代表基金总收益或个人交易盈亏。
                    </small>
                    {diagnosis.data.navChanges.reasons.map((reason) => (
                      <p key={reason}>{reason}</p>
                    ))}
                  </>
                )}
              <p>
                63观察日收益：
                {metricValue(
                  diagnosis.data?.performance?.return63 == null
                    ? null
                    : diagnosis.data.performance.return63 * 100,
                )}
                % · 252观察日收益：
                {metricValue(
                  diagnosis.data?.performance?.return252 == null
                    ? null
                    : diagnosis.data.performance.return252 * 100,
                )}
                %
              </p>
              <p>
                样本最大回撤：
                {metricValue(
                  diagnosis.data?.performance?.maxDrawdown == null
                    ? null
                    : diagnosis.data.performance.maxDrawdown * 100,
                )}
                % · 实际观察：
                {diagnosis.data?.performance?.sampleCount ?? "未知"}
              </p>
            </details>
          </aside>
        </div>
        {add.isError && watchActionCurrent && (
          <p role="alert">加入自选失败，请重试。</p>
        )}
      </details>
    </div>
  );
}

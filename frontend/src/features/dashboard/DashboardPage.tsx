import {
  AlertTriangle,
  ArrowRightLeft,
  CircleDashed,
  Download,
  RefreshCw,
  Upload,
} from "lucide-react";
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  useNavTrend,
  usePortfolioSummary,
  usePositions,
  useMe,
  useConfirmTiantianHoldingStart,
  useConfirmTiantianHoldingStartEstimates,
} from "../../api/hooks";
import type { PortfolioAssetScope } from "../../api/types";
import PageHeader from "../../components/layout/PageHeader";
import AssetDonutChart from "./components/AssetDonutChart";
import EquityCurveChart from "./components/EquityCurveChart";
import HoldingsTable from "./components/HoldingsTable";
import MetricCards from "./components/MetricCards";
import SentimentCard from "./components/SentimentCard";
import AdviceCard from "./components/AdviceCard";
import AssetScopeSwitcher from "./components/AssetScopeSwitcher";
import WatchlistPanel from "./components/WatchlistPanel";
import { useAiAdvice, useMarketSentiment } from "../../api/hooks";
import { createIdempotencyKey } from "../../lib/idempotency";

export default function DashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const me = useMe();
  const scope = useMemo<PortfolioAssetScope>(() => {
    const kind = searchParams.get("scope")?.toUpperCase();
    const memberId = searchParams.get("memberId") ?? undefined;
    if (kind === "TENANT") return { kind: "TENANT" };
    if (kind === "MEMBER" && memberId) return { kind: "MEMBER", memberId };
    return { kind: "MINE" };
  }, [searchParams]);
  const summary = usePortfolioSummary(scope);
  const positions = usePositions(scope);
  const navTrend = useNavTrend(
    30,
    scope,
    summary.data?.valuationStatus === "SYNCING",
  );
  const sentiment = useMarketSentiment();
  const advice = useAiAdvice();
  const confirmHoldingStart = useConfirmTiantianHoldingStart();
  const confirmHoldingStartEstimates =
    useConfirmTiantianHoldingStartEstimates();
  const activeTenantName =
    me.data?.tenants.find(
      (tenant) => tenant.tenantId === me.data?.activeTenantId,
    )?.name ?? "";
  const selectedMemberName =
    scope.kind === "MEMBER"
      ? me.data?.members.find((member) => member.userId === scope.memberId)
          ?.nickname
      : null;
  const isEmptyPortfolio =
    summary.data?.qualityStatus === "MISSING" &&
    summary.data.positionCount === 0 &&
    summary.data.asOf === null;
  const errors = [
    summary.error,
    positions.error,
    navTrend.error,
    sentiment.error,
    advice.error,
  ]
    .filter((error): error is Error => error instanceof Error)
    .map((error) => error.message);

  return (
    <>
      <PageHeader
        title="资产看板"
        description="股票、场内基金与场外基金的统一资产视图 · 实时接入后端数据"
        actions={
          <>
            <Link className="btn-secondary" to="/onboarding">
              <Upload size={14} />
              导入 / 更新持仓
            </Link>
            <button
              className="btn-secondary"
              disabled
              title="快照导出接口尚未接入"
            >
              <Download size={14} />
              导出快照
            </button>
          </>
        }
      />
      <AssetScopeSwitcher
        scope={scope}
        tenantName={activeTenantName}
        members={me.data?.members ?? []}
        onChange={(next) => {
          const params = new URLSearchParams(searchParams);
          params.set("scope", next.kind);
          if (next.kind === "MEMBER" && next.memberId) {
            params.set("memberId", next.memberId);
          } else {
            params.delete("memberId");
          }
          setSearchParams(params, { replace: true });
        }}
      />
      {errors.length > 0 ? (
        <div className="alert alert-warning">
          <AlertTriangle size={16} />
          <div>
            <strong>资产数据暂不可用</strong>
            <p>{[...new Set(errors)].join("；")}</p>
          </div>
        </div>
      ) : null}
      {isEmptyPortfolio ? (
        <section
          className="empty-state panel"
          aria-labelledby="empty-portfolio-title"
        >
          <Upload size={28} />
          <h2 id="empty-portfolio-title">
            {scope.kind === "MINE"
              ? "你还没有归属到自己的资产"
              : scope.kind === "MEMBER"
                ? `${selectedMemberName ?? "该成员"}暂无归属资产`
                : "当前租户尚未录入资产"}
          </h2>
          <p>
            {scope.kind === "MINE"
              ? "新建或导入个人账户后，这里会展示你的资产；历史待认领账户可在租户总览查看。"
              : "切换查看范围，或完成首笔期初持仓导入后查看资产汇总和净值走势。"}
          </p>
          <Link className="btn-primary" to="/onboarding">
            导入首笔持仓
          </Link>
        </section>
      ) : summary.data?.valuationStatus === "SYNCING" ? (
        <div className="alert alert-info" role="status">
          <RefreshCw size={16} aria-hidden="true" />
          <div>
            <strong>估值正在自动更新</strong>
            <p>
              {summary.data.valuationPendingCount} 个持仓的官方净值已经获取，
              系统正在同步市值和总资产；预计 1 分钟内完成。无需操作，
              页面会自动刷新。
            </p>
          </div>
        </div>
      ) : summary.data && summary.data.totalAsset === null ? (
        <div className="alert alert-warning">
          <AlertTriangle size={16} />
          <div>
            <strong>总资产暂不发布</strong>
            <p>
              仍有 {summary.data.unpricedPositionCount} 个持仓缺少可靠估值；
              当前已知金额 {summary.data.knownValue} 元，估值覆盖率
              {summary.data.valuationCoveragePct}%。
            </p>
          </div>
        </div>
      ) : null}
      <MetricCards summary={summary.data} loading={summary.isLoading} />
      <SentimentCard
        sentiment={sentiment.data}
        loading={sentiment.isLoading}
        error={
          sentiment.error instanceof Error ? sentiment.error.message : null
        }
      />
      <AdviceCard
        advice={advice.data}
        loading={advice.isLoading}
        refreshing={advice.isFetching && !advice.isLoading}
        error={advice.error instanceof Error ? advice.error.message : null}
        onRefresh={() => void advice.refetch()}
      />
      <WatchlistPanel />
      <div className="dashboard-chart-grid">
        <EquityCurveChart
          points={navTrend.error ? [] : (navTrend.data?.points ?? [])}
          loading={navTrend.isLoading}
        />
        <AssetDonutChart
          allocation={summary.error ? [] : (summary.data?.allocation ?? [])}
          loading={summary.isLoading}
        />
      </div>
      <HoldingsTable
        rows={positions.error ? [] : (positions.data ?? [])}
        loading={positions.isLoading}
        onConfirmHoldingStart={async (position, startedOn) => {
          return await confirmHoldingStart.mutateAsync({
            accountId: position.accountId,
            instrumentId: position.instrumentId,
            startedOn,
            idempotencyKey: createIdempotencyKey(),
          });
        }}
        onConfirmEstimatedHoldingStarts={async (items) => {
          await confirmHoldingStartEstimates.mutateAsync({
            items,
            idempotencyKey: createIdempotencyKey(),
          });
        }}
      />
      <div className="dashboard-actions panel">
        <div className="dashboard-action-copy">
          <strong>资产事实维护</strong>
          <span>手工动作均先进入草稿或预览，不直接覆盖账本事实。</span>
          <div
            className="dashboard-action-status"
            role="note"
            aria-label="成交录入状态"
          >
            <CircleDashed size={12} aria-hidden="true" />
            <span>成交确认暂未开放，当前操作不会修改账本。</span>
          </div>
        </div>
        <div className="action-buttons">
          <button className="btn-secondary" disabled title="出入金命令尚未接入">
            <ArrowRightLeft size={14} />
            登记出入金
          </button>
          <button
            className="btn-secondary"
            disabled
            title="成交草稿命令尚未接入"
          >
            录入成交草稿
          </button>
          <button
            className="btn-secondary"
            disabled
            title="由四时段 Worker 刷新行情"
          >
            <RefreshCw size={14} />
            刷新行情
          </button>
        </div>
      </div>
    </>
  );
}

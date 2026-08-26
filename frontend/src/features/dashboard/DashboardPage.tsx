import {
  AlertTriangle,
  ArrowRightLeft,
  Download,
  RefreshCw,
  Upload,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  useNavTrend,
  usePortfolioSummary,
  usePositions,
} from "../../api/hooks";
import PageHeader from "../../components/layout/PageHeader";
import AssetDonutChart from "./components/AssetDonutChart";
import EquityCurveChart from "./components/EquityCurveChart";
import HoldingsTable from "./components/HoldingsTable";
import MetricCards from "./components/MetricCards";

export default function DashboardPage() {
  const summary = usePortfolioSummary();
  const positions = usePositions();
  const navTrend = useNavTrend(30);
  const errors = [summary.error, positions.error, navTrend.error]
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
              初始化导入
            </Link>
            <button className="btn-secondary" disabled title="快照导出接口尚未接入">
              <Download size={14} />
              导出快照
            </button>
          </>
        }
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
      {summary.data && summary.data.totalAsset === null ? (
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
      />
      <div className="dashboard-actions panel">
        <div>
          <strong>资产事实维护</strong>
          <span>手工动作均先进入草稿或预览，不直接覆盖账本事实。</span>
          <small>成交确认命令尚未接入，当前不会修改账本</small>
        </div>
        <div className="action-buttons">
          <button className="btn-secondary" disabled title="出入金命令尚未接入">
            <ArrowRightLeft size={14} />
            登记出入金
          </button>
          <button className="btn-secondary" disabled title="成交草稿命令尚未接入">
            录入成交草稿
          </button>
          <button className="btn-secondary" disabled title="由四时段 Worker 刷新行情">
            <RefreshCw size={14} />
            刷新行情
          </button>
        </div>
      </div>
    </>
  );
}

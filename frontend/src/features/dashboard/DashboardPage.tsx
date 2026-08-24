import { useState } from "react";
import {
  AlertTriangle,
  ArrowRightLeft,
  Download,
  RefreshCw,
  Upload,
} from "lucide-react";
import { Link } from "react-router-dom";
import type { SettlementPreviewDto } from "../../api/types";
import { previewSettlement } from "../../api/portfolio";
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
import TradeFillConfirmModal from "./components/TradeFillConfirmModal";
import { pendingFills } from "./pending-fills";

export default function DashboardPage() {
  const summary = usePortfolioSummary();
  const positions = usePositions();
  const navTrend = useNavTrend(30);
  const [fills, setFills] = useState(pendingFills);
  const [modalOpen, setModalOpen] = useState(false);
  const [previews, setPreviews] = useState<Map<string, SettlementPreviewDto>>(
    () => new Map(),
  );

  async function openFillReview() {
    setModalOpen(true);
    const nextPreviews = await Promise.all(
      fills.map((fill) => previewSettlement(fill, "201382.15")),
    );
    setPreviews(
      new Map(nextPreviews.map((preview) => [preview.fillId, preview])),
    );
  }

  const dataUnavailable =
    summary.error &&
    summary.error instanceof Error &&
    summary.error.message;

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
            <button className="btn-secondary">
              <Download size={14} />
              导出快照
            </button>
          </>
        }
      />
      {dataUnavailable ? (
        <div className="alert alert-warning">
          <AlertTriangle size={16} />
          <div>
            <strong>资产数据暂不可用</strong>
            <p>{dataUnavailable}</p>
          </div>
        </div>
      ) : null}
      <MetricCards summary={summary.data} loading={summary.isLoading} />
      <div className="dashboard-chart-grid">
        <EquityCurveChart
          points={navTrend.data?.points ?? []}
          loading={navTrend.isLoading}
        />
        <AssetDonutChart
          allocation={summary.data?.allocation ?? []}
          loading={summary.isLoading}
        />
      </div>
      <HoldingsTable rows={positions.data ?? []} loading={positions.isLoading} />
      <div className="dashboard-actions panel">
        <div>
          <strong>资产事实维护</strong>
          <span>手工动作均先进入草稿或预览，不直接覆盖账本事实。</span>
        </div>
        <div className="action-buttons">
          <button className="btn-secondary">
            <ArrowRightLeft size={14} />
            登记出入金
          </button>
          <button className="btn-secondary" onClick={openFillReview}>
            录入成交草稿
          </button>
          <button className="btn-secondary">
            <RefreshCw size={14} />
            刷新行情
          </button>
          <button
            className="btn-primary"
            aria-label={`${fills.length}笔草稿`}
            onClick={openFillReview}
          >
            {fills.length}笔草稿
          </button>
        </div>
      </div>
      <TradeFillConfirmModal
        open={modalOpen}
        fills={fills}
        previews={previews}
        onClose={() => setModalOpen(false)}
        onConfirm={(id) =>
          setFills((current) => current.filter((fill) => fill.id !== id))
        }
        onInvalidateAll={() => setFills([])}
      />
    </>
  );
}

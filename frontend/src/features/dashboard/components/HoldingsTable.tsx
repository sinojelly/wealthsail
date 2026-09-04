import { Fragment, useMemo, useState } from "react";
import { BadgeCheck, Plus, Search, Upload, WalletCards } from "lucide-react";
import Decimal from "decimal.js";
import { Link } from "react-router-dom";
import QualityBadge from "../../../components/data-state/QualityBadge";
import EmptyState from "../../../components/data-state/EmptyState";
import { formatMoney, formatSignedMoney } from "../../../domain/money";
import type {
  HoldingStartEstimateConfirmation,
  HoldingStartConfirmationResult,
  PositionDto,
} from "../../../api/types";
import PositionDialog from "./PositionDialog";
import FundRangeMetrics from "./FundRangeMetrics";
import FundIntradayEstimate from "./FundIntradayEstimate";

function holdingPnlTone(value: string | null): string {
  if (value === null) {
    return "pnl-flat";
  }
  const pnl = new Decimal(value);
  if (pnl.isZero()) {
    return "pnl-flat";
  }
  if (pnl.isPositive()) {
    return "pnl-gain";
  }
  if (pnl.isNegative()) {
    return "pnl-loss";
  }
  return "pnl-flat";
}

export default function HoldingsTable({
  rows,
  loading,
  onConfirmHoldingStart,
  onConfirmEstimatedHoldingStarts,
}: {
  rows: PositionDto[];
  loading?: boolean;
  onConfirmHoldingStart?: (
    position: PositionDto,
    startedOn: string,
  ) => Promise<HoldingStartConfirmationResult>;
  onConfirmEstimatedHoldingStarts?: (
    items: HoldingStartEstimateConfirmation[],
  ) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("全部");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmingEstimates, setConfirmingEstimates] = useState(false);
  const [batchMessage, setBatchMessage] = useState<string | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);

  const filtered = useMemo(
    () =>
      rows.filter((row) => {
        const matchesQuery = `${row.symbol}${row.name}`
          .toLowerCase()
          .includes(query.toLowerCase());
        return matchesQuery && (type === "全部" || row.type === type);
      }),
    [query, rows, type],
  );
  const estimatedHoldingStarts = useMemo<HoldingStartEstimateConfirmation[]>(
    () =>
      rows.flatMap((row) => {
        const startedOn = row.holdingPeriod?.startedOn;
        const isEstimatedWarning =
          row.type === "场外基金" &&
          Boolean(row.holdingPeriod?.canConfirm) &&
          Boolean(startedOn) &&
          row.rangeMetrics?.status === "PARTIAL" &&
          row.rangeMetrics.qualityReasons.includes("PARTIAL_HOLDING_HISTORY");
        return isEstimatedWarning && startedOn
          ? [
              {
                accountId: row.accountId,
                instrumentId: row.instrumentId,
                startedOn,
              },
            ]
          : [];
      }),
    [rows],
  );

  function openAdd() {
    setDialogOpen(true);
  }

  async function confirmAllEstimates() {
    if (
      !onConfirmEstimatedHoldingStarts ||
      estimatedHoldingStarts.length === 0
    ) {
      return;
    }
    setConfirmingEstimates(true);
    setBatchMessage(null);
    setBatchError(null);
    try {
      await onConfirmEstimatedHoldingStarts(estimatedHoldingStarts);
      setBatchMessage(
        `已确认 ${estimatedHoldingStarts.length} 个估算日期，未触发净值重算。`,
      );
    } catch (error) {
      setBatchError(
        error instanceof Error ? error.message : "批量确认失败，请刷新后重试。",
      );
    } finally {
      setConfirmingEstimates(false);
    }
  }

  return (
    <section className="panel holdings-panel">
      <div className="panel-header holdings-toolbar">
        <div>
          <h2>当前持仓</h2>
          <p>股票、场内 ETF 与场外基金统一视图</p>
        </div>
        <div className="table-filters">
          <label className="search-input">
            <Search size={13} />
            <input
              aria-label="搜索持仓"
              value={query}
              placeholder="代码 / 名称"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <select
            aria-label="持仓类型"
            className="select compact"
            value={type}
            onChange={(event) => setType(event.target.value)}
          >
            <option>全部</option>
            <option>A股</option>
            <option>场内ETF</option>
            <option>场外基金</option>
          </select>
          <Link className="btn-primary" to="/onboarding">
            <Upload size={14} />
            导入 / 更新持仓
          </Link>
          <button className="btn-secondary" type="button" onClick={openAdd}>
            <Plus size={14} />
            手工初始化
          </button>
        </div>
      </div>
      {estimatedHoldingStarts.length > 0 && onConfirmEstimatedHoldingStarts ? (
        <div className="holding-start-batch-banner">
          <BadgeCheck size={18} aria-hidden="true" />
          <div>
            <strong>
              有 {estimatedHoldingStarts.length} 只基金的持有起点来自系统估算
            </strong>
            <span>
              认可系统推荐日期，可一次确认并消除提示；确认本身不会重新抓取净值或重算指标。
            </span>
          </div>
          <button
            className="btn-secondary"
            type="button"
            disabled={confirmingEstimates}
            onClick={() => void confirmAllEstimates()}
          >
            {confirmingEstimates
              ? "正在确认…"
              : `确认全部估算日期（${estimatedHoldingStarts.length}）`}
          </button>
        </div>
      ) : null}
      {batchMessage ? (
        <p className="holding-start-batch-message" role="status">
          {batchMessage}
        </p>
      ) : null}
      {batchError ? (
        <p className="holding-start-batch-error" role="alert">
          {batchError}
        </p>
      ) : null}
      <div className="table-wrap">
        {loading ? (
          <div className="table-placeholder dashboard-data-loading">
            加载中…
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            className="holdings-empty-state"
            title={rows.length === 0 ? "还没有持仓" : "没有匹配的持仓"}
            detail={
              rows.length === 0
                ? "导入天天基金 ZIP，或手工创建并确认期初基线。"
                : "请调整搜索关键字或持仓类型后重试。"
            }
            icon={WalletCards}
          />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>标的</th>
                <th>类型</th>
                <th>账户 / 归属</th>
                <th className="numeric">持有份额</th>
                <th className="numeric">成本价</th>
                <th className="numeric">最新价/净值</th>
                <th className="numeric">市值</th>
                <th className="numeric">占比</th>
                <th className="numeric">持仓收益</th>
                <th>质量</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <Fragment key={`${row.symbol}-${row.accountId}`}>
                  <tr>
                    <td>
                      <div className="security-cell">
                        <strong>{row.name}</strong>
                        <span className="mono">{row.symbol}</span>
                      </div>
                    </td>
                    <td>
                      <span className="tag">{row.type}</span>
                    </td>
                    <td>
                      <div className="account-owner-cell">
                        <strong>{row.account.name ?? "未命名账户"}</strong>
                        <span>
                          {row.account.ownershipScope === "UNASSIGNED"
                            ? "待认领"
                            : row.account.ownershipScope === "SHARED"
                              ? `共同 · ${row.account.owners.map((owner) => owner.name ?? "未命名成员").join("、")}`
                              : (row.account.owners[0]?.name ?? "归属待核对")}
                        </span>
                      </div>
                    </td>
                    <td className="mono numeric">{row.quantity}</td>
                    <td className="mono numeric">{row.avgCost}</td>
                    <td className="mono numeric">{row.price ?? "—"}</td>
                    <td className="mono numeric">
                      {row.marketValue ? formatMoney(row.marketValue) : "—"}
                    </td>
                    <td className="mono numeric">
                      {row.weight ? `${row.weight}%` : "—"}
                    </td>
                    <td
                      className={`mono numeric holding-pnl ${holdingPnlTone(row.pnl)}`}
                    >
                      <strong>
                        {row.pnl ? formatSignedMoney(row.pnl) : "—"}
                      </strong>
                      <small className="holding-pnl-rate">
                        {row.pnlRate ? `${row.pnlRate}%` : "—"}
                      </small>
                    </td>
                    <td>
                      {row.valuationStatus === "SYNCING" ? (
                        <span
                          className="quality-badge valuation-syncing"
                          title="官方净值已获取，系统正在自动同步市值"
                        >
                          估值同步中
                        </span>
                      ) : (
                        <QualityBadge quality={row.quality} />
                      )}
                    </td>
                  </tr>
                  {row.type === "场外基金" ? (
                    <tr className="fund-range-row">
                      <td colSpan={10}>
                        <FundIntradayEstimate estimate={row.intradayEstimate} />
                        <FundRangeMetrics
                          holdingPeriod={row.holdingPeriod}
                          metrics={row.rangeMetrics}
                          currentNav={row.price}
                          costReturnPct={row.pnlRate}
                          onConfirmHoldingStart={
                            onConfirmHoldingStart
                              ? (startedOn) =>
                                  onConfirmHoldingStart(row, startedOn)
                              : undefined
                          }
                        />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <PositionDialog
        key={`position-import-${String(dialogOpen)}`}
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
      />
    </section>
  );
}

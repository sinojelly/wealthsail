import { Fragment, useMemo, useState } from "react";
import { Plus, Search, Upload, WalletCards } from "lucide-react";
import { Link } from "react-router-dom";
import QualityBadge from "../../../components/data-state/QualityBadge";
import EmptyState from "../../../components/data-state/EmptyState";
import { formatMoney, formatSignedMoney } from "../../../domain/money";
import type { PositionDto } from "../../../api/types";
import PositionDialog from "./PositionDialog";
import FundRangeMetrics from "./FundRangeMetrics";

export default function HoldingsTable({
  rows,
  loading,
}: {
  rows: PositionDto[];
  loading?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [type, setType] = useState("全部");
  const [dialogOpen, setDialogOpen] = useState(false);

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

  function openAdd() {
    setDialogOpen(true);
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
                              : row.account.owners[0]?.name ?? "归属待核对"}
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
                      className={`mono numeric ${row.pnl?.startsWith("-") ? "negative" : row.pnl ? "positive" : ""}`}
                    >
                      <strong>
                        {row.pnl ? formatSignedMoney(row.pnl) : "—"}
                      </strong>
                      <small>{row.pnlRate ? `${row.pnlRate}%` : "—"}</small>
                    </td>
                    <td>
                      <QualityBadge quality={row.quality} />
                    </td>
                  </tr>
                  {row.type === "场外基金" ? (
                    <tr className="fund-range-row">
                      <td colSpan={10}>
                        <FundRangeMetrics
                          holdingPeriod={row.holdingPeriod}
                          metrics={row.rangeMetrics}
                          currentNav={row.price}
                          costReturnPct={row.pnlRate}
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

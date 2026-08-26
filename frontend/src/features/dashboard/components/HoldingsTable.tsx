import { useMemo, useState } from "react";
import { Pencil, Plus, Search, Trash2 } from "lucide-react";
import QualityBadge from "../../../components/data-state/QualityBadge";
import { formatMoney, formatSignedMoney } from "../../../domain/money";
import { useDeletePosition } from "../../../api/hooks";
import type { PositionDto } from "../../../api/types";
import PositionDialog from "./PositionDialog";

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
  const [dialogMode, setDialogMode] = useState<"add" | "edit">("add");
  const [editing, setEditing] = useState<PositionDto | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const deleteMutation = useDeletePosition();

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
    setEditing(null);
    setDialogMode("add");
    setActionError(null);
    setDialogOpen(true);
  }

  function openEdit(row: PositionDto) {
    setEditing(row);
    setDialogMode("edit");
    setActionError(null);
    setDialogOpen(true);
  }

  async function handleDelete(row: PositionDto) {
    if (
      !window.confirm(
        `确认移除 ${row.name}（${row.symbol}，${row.quantity} 份）？\n该操作会写入账本并重算资产，不可撤销。`,
      )
    ) {
      return;
    }
    setActionError(null);
    try {
      await deleteMutation.mutateAsync(row.symbol);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "删除失败，请稍后重试");
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
          <button className="btn-primary" type="button" onClick={openAdd}>
            <Plus size={14} />
            添加持仓
          </button>
        </div>
      </div>
      {actionError ? (
        <div className="alert alert-danger">{actionError}</div>
      ) : null}
      <div className="table-wrap">
        {loading ? (
          <div className="table-placeholder">加载中…</div>
        ) : filtered.length === 0 ? (
          <div className="table-placeholder">
            {rows.length === 0
              ? "暂无持仓，点击「添加持仓」录入第一笔"
              : "无匹配持仓"}
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>标的</th>
                <th>类型</th>
                <th className="numeric">持有份额</th>
                <th className="numeric">成本价</th>
                <th className="numeric">最新价/净值</th>
                <th className="numeric">市值</th>
                <th className="numeric">占比</th>
                <th className="numeric">持仓收益</th>
                <th>质量</th>
                <th className="actions-col">操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <tr key={`${row.symbol}-${row.accountId}`}>
                  <td>
                    <div className="security-cell">
                      <strong>{row.name}</strong>
                      <span className="mono">{row.symbol}</span>
                    </div>
                  </td>
                  <td>
                    <span className="tag">{row.type}</span>
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
                    <strong>{row.pnl ? formatSignedMoney(row.pnl) : "—"}</strong>
                    <small>{row.pnlRate ? `${row.pnlRate}%` : "—"}</small>
                  </td>
                  <td>
                    <QualityBadge quality={row.quality} />
                  </td>
                  <td className="actions-col">
                    <div className="row-actions">
                      <button
                        className="icon-button"
                        type="button"
                        aria-label={`编辑 ${row.name}`}
                        title="编辑数量/成本"
                        onClick={() => openEdit(row)}
                      >
                        <Pencil size={14} />
                      </button>
                      <button
                        className="icon-button danger"
                        type="button"
                        aria-label={`删除 ${row.name}`}
                        title="删除持仓"
                        onClick={() => void handleDelete(row)}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <PositionDialog
        key={`${dialogMode}-${editing?.symbol ?? "new"}-${String(dialogOpen)}`}
        open={dialogOpen}
        mode={dialogMode}
        initial={editing}
        onClose={() => setDialogOpen(false)}
      />
    </section>
  );
}

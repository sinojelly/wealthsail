import type { ActionableZoneResult } from "../../../api/investment";
import { fundDisplayName, planPrice } from "../plan-display";
import { fundOperationDistance } from "../fund-operation-distance";
import { finitePrice } from "../quantitative-position";
import "./OperationQuickTable.css";

export interface OperationQuickRow {
  id: string;
  code: string;
  name: string;
  status: string;
  tone?: string;
  plan?: ActionableZoneResult | null;
  currentNav?: number | string | null;
  holdingReturnPct?: number | string | null;
  advice: string;
}

function percent(value: number | string | null | undefined, signed = false) {
  if (value == null || value === "" || !Number.isFinite(Number(value)))
    return "—";
  const pct = Number(value);
  return `${signed && pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

export default function OperationQuickTable({
  rows,
  onSelect,
}: {
  rows: readonly OperationQuickRow[];
  onSelect: (id: string) => void;
}) {
  return (
    <section className="op-operation-quick" aria-label="今日操作速览表">
      <h3>⚡ 今日操作速览</h3>
      {rows.length ? (
        <div
          className="op-operation-quick-scroll"
          tabIndex={0}
          role="region"
          aria-label="滚动查看全部操作"
        >
          <table aria-label="今日操作速览">
            <thead>
              <tr>
                <th scope="col">基金名称与代码</th>
                <th scope="col">状态</th>
                <th scope="col">当前净值（近两年分位）</th>
                <th scope="col">参考买区／卖区</th>
                <th scope="col">参考止盈空间</th>
                <th scope="col">我的持仓盈亏</th>
                <th scope="col">操作参考</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const position = fundOperationDistance(
                  row.plan,
                  row.currentNav,
                );
                const current =
                  row.currentNav === undefined
                    ? position.current
                    : finitePrice(row.currentNav);
                const firstTarget =
                  position.sellLow != null && current != null
                    ? position.sellLow / current - 1
                    : null;
                const format = (value: number | null) =>
                  value == null
                    ? "—"
                    : row.plan
                      ? planPrice(value, row.plan)
                      : value.toFixed(4);
                const range = (low: number | null, high: number | null) =>
                  low == null || high == null
                    ? "—"
                    : `${format(low)}–${format(high)}`;
                return (
                  <tr key={row.id} onClick={() => onSelect(row.id)}>
                    <th scope="row">
                      <button type="button">
                        {fundDisplayName(row.name, row.code)}{" "}
                        <small>{row.code}</small>
                      </button>
                    </th>
                    <td>
                      <span className="op-position-badge" data-tone={row.tone}>
                        {row.status}
                      </span>
                    </td>
                    <td>
                      {format(current)}
                      <small>{percent(position.percentile)}</small>
                    </td>
                    <td>
                      <span>买 {range(position.low, position.high)}</span>
                      <small>
                        卖 {range(position.sellLow, position.sellHigh)}
                      </small>
                    </td>
                    <td>
                      {firstTarget == null
                        ? "—"
                        : firstTarget <= 0
                          ? "已达卖区"
                          : percent(firstTarget * 100, true)}
                    </td>
                    <td>{percent(row.holdingReturnPct, true)}</td>
                    <td>{row.advice}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p>暂无达到买区、卖区或趋势条件的基金。</p>
      )}
    </section>
  );
}

import { useState } from "react";
import { Loader2, Plus, Save, Search, X } from "lucide-react";
import Dialog from "../../../components/accessibility/Dialog";
import {
  useAddPosition,
  useInstrumentSearch,
  useUpdatePosition,
} from "../../../api/hooks";
import type {
  InstrumentSearchResult,
  PositionDto,
} from "../../../api/types";

interface PositionDialogProps {
  open: boolean;
  mode: "add" | "edit";
  initial?: PositionDto | null;
  onClose: () => void;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function isValidNumber(value: string): boolean {
  return /^\d+(\.\d+)?$/.test(value.trim());
}

export default function PositionDialog({
  open,
  mode,
  initial,
  onClose,
}: PositionDialogProps) {
  const isAdd = mode === "add";
  // 组件由父级以 key 重挂载（每次打开重置状态），此处直接以 props 初始化。
  const [searchQuery, setSearchQuery] = useState("");
  const [selected, setSelected] = useState<InstrumentSearchResult | null>(null);
  const [quantity, setQuantity] = useState(initial ? initial.quantity : "");
  const [avgCost, setAvgCost] = useState(initial ? initial.avgCost : "");
  const [buyDate, setBuyDate] = useState(todayIso());
  const [error, setError] = useState<string | null>(null);

  const addMutation = useAddPosition();
  const updateMutation = useUpdatePosition();
  const search = useInstrumentSearch(
    searchQuery,
    isAdd && !selected && open,
  );

  const submitting = addMutation.isPending || updateMutation.isPending;

  function pickInstrument(item: InstrumentSearchResult) {
    setSelected(item);
    setSearchQuery("");
    setError(null);
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!isValidNumber(quantity) || Number(quantity) <= 0) {
      setError("数量必须为大于 0 的数字");
      return;
    }
    if (!isValidNumber(avgCost) || Number(avgCost) < 0) {
      setError("成本价必须为不小于 0 的数字");
      return;
    }
    setError(null);
    try {
      if (isAdd) {
        if (!selected) {
          setError("请先搜索并选择标的");
          return;
        }
        await addMutation.mutateAsync({
          symbol: selected.symbol,
          name: selected.name,
          assetType: selected.type,
          quantity: quantity.trim(),
          avgCost: avgCost.trim(),
          buyDate: buyDate || undefined,
          idempotencyKey: crypto.randomUUID(),
        });
      } else if (initial) {
        await updateMutation.mutateAsync({
          symbol: initial.symbol,
          payload: {
            quantity: quantity.trim(),
            avgCost: avgCost.trim(),
            buyDate: buyDate || undefined,
            idempotencyKey: crypto.randomUUID(),
          },
        });
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败，请稍后重试");
    }
  }

  const title = isAdd ? "添加持仓" : `编辑持仓 · ${initial?.symbol ?? ""}`;

  return (
    <Dialog open={open} title={title} onClose={onClose} size="md">
      <form className="dialog-content" onSubmit={handleSubmit}>
        {isAdd && (
          <div className="field">
            <label htmlFor="position-search">搜索标的（代码或名称）</label>
            {selected ? (
              <div className="security-cell selected-security">
                <div>
                  <strong>{selected.name}</strong>
                  <span className="mono">
                    {selected.symbol} · {selected.market} ·{" "}
                    {selected.type === "STOCK"
                      ? "股票"
                      : selected.type === "ETF"
                        ? "场内ETF"
                        : "场外基金"}
                  </span>
                </div>
                <button
                  className="icon-button"
                  type="button"
                  aria-label="重新选择标的"
                  onClick={() => setSelected(null)}
                >
                  <X size={14} />
                </button>
              </div>
            ) : (
              <>
                <label className="search-input">
                  <Search size={13} />
                  <input
                    id="position-search"
                    value={searchQuery}
                    placeholder="例如：茅台 / 600519 / 510300"
                    onChange={(event) => setSearchQuery(event.target.value)}
                    autoFocus
                  />
                </label>
                {searchQuery.trim().length >= 1 && (
                  <div className="search-results" role="listbox">
                    {search.isLoading ? (
                      <div className="search-result-hint">
                        <Loader2 className="spin" size={13} /> 搜索中…
                      </div>
                    ) : search.error ? (
                      <div className="search-result-hint">
                        搜索服务暂不可用，请稍后重试
                      </div>
                    ) : (search.data ?? []).length === 0 ? (
                      <div className="search-result-hint">未找到匹配标的</div>
                    ) : (
                      (search.data ?? []).map((item) => (
                        <button
                          type="button"
                          role="option"
                          className="search-result-row"
                          key={`${item.symbol}-${item.type}`}
                          onClick={() => pickInstrument(item)}
                        >
                          <span className="security-cell">
                            <strong>{item.name}</strong>
                            <span className="mono">{item.symbol}</span>
                          </span>
                          <span className="tag">
                            {item.type === "STOCK"
                              ? "股票"
                              : item.type === "ETF"
                                ? "ETF"
                                : "基金"}
                          </span>
                        </button>
                      ))
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {!isAdd && initial && (
          <div className="security-cell selected-security static">
            <div>
              <strong>{initial.name}</strong>
              <span className="mono">{initial.symbol}</span>
            </div>
          </div>
        )}

        <div className="form-grid">
          <div className="field">
            <label htmlFor="position-quantity">数量 / 份额 *</label>
            <input
              id="position-quantity"
              className="input"
              inputMode="decimal"
              value={quantity}
              placeholder="例如 100"
              onChange={(event) => setQuantity(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="position-avg-cost">成本价（每股/每份）*</label>
            <input
              id="position-avg-cost"
              className="input"
              inputMode="decimal"
              value={avgCost}
              placeholder="例如 1500.00"
              onChange={(event) => setAvgCost(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="position-buy-date">买入日期（可选）</label>
            <input
              id="position-buy-date"
              className="input"
              type="date"
              value={buyDate}
              max={todayIso()}
              onChange={(event) => setBuyDate(event.target.value)}
            />
          </div>
        </div>

        {error ? <div className="alert alert-danger">{error}</div> : null}

        <div className="dialog-actions">
          <button
            className="btn-secondary"
            type="button"
            disabled={submitting}
            onClick={onClose}
          >
            取消
          </button>
          <button className="btn-primary" type="submit" disabled={submitting}>
            {submitting ? (
              <>
                <Loader2 className="spin" size={14} /> 提交中…
              </>
            ) : isAdd ? (
              <>
                <Plus size={14} /> 确认添加
              </>
            ) : (
              <>
                <Save size={14} /> 保存修改
              </>
            )}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

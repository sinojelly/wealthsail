import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, Plus, Search, ShieldCheck, X } from "lucide-react";
import Dialog from "../../../components/accessibility/Dialog";
import {
  useAddPosition,
  useConfirmPositionImport,
  useInstrumentSearch,
} from "../../../api/hooks";
import type {
  InstrumentSearchResult,
  PositionImportDraft,
} from "../../../api/types";

interface PositionDialogProps {
  open: boolean;
  onClose: () => void;
}

/** Return the user's local calendar day; UTC conversion can otherwise shift China dates. */
function todayLocalIso(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function isValidNumber(value: string): boolean {
  return /^\d+(\.\d+)?$/.test(value.trim());
}

export default function PositionDialog({ open, onClose }: PositionDialogProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [selected, setSelected] = useState<InstrumentSearchResult | null>(null);
  const [quantity, setQuantity] = useState("");
  const [avgCost, setAvgCost] = useState("");
  const [baselineDate, setBaselineDate] = useState(todayLocalIso());
  const [draft, setDraft] = useState<PositionImportDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  // A retry of the same user intent must reuse its key. A new dialog mount gets new keys.
  const [draftKey] = useState(() => crypto.randomUUID());
  const [confirmKey] = useState(() => crypto.randomUUID());

  const createMutation = useAddPosition();
  const confirmMutation = useConfirmPositionImport();
  const search = useInstrumentSearch(
    debouncedQuery,
    !selected && !draft && open,
  );

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedQuery(searchQuery.trim()),
      300,
    );
    return () => window.clearTimeout(timer);
  }, [searchQuery]);

  const submitting = createMutation.isPending || confirmMutation.isPending;
  const formValid =
    selected !== null &&
    isValidNumber(quantity) &&
    Number(quantity) > 0 &&
    isValidNumber(avgCost) &&
    Number(avgCost) >= 0;

  function pickInstrument(item: InstrumentSearchResult) {
    setSelected(item);
    setSearchQuery("");
    setDebouncedQuery("");
    setError(null);
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!selected) {
      setError("请先搜索并选择标的");
      return;
    }
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
      const created = await createMutation.mutateAsync({
        payload: {
          symbol: selected.symbol,
          name: selected.name,
          assetType: selected.type,
          quantity: quantity.trim(),
          avgCost: avgCost.trim(),
          buyDate: baselineDate || undefined,
        },
        idempotencyKey: draftKey,
      });
      setDraft(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建草稿失败，请稍后重试");
    }
  }

  async function handleConfirm() {
    if (!draft) return;
    setError(null);
    try {
      await confirmMutation.mutateAsync({
        batchId: draft.batchId,
        idempotencyKey: confirmKey,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "确认失败，请稍后重试");
    }
  }

  return (
    <Dialog open={open} title="初始化一笔持仓" onClose={onClose} size="md">
      {draft ? (
        <div className="dialog-content">
          <div className="alert alert-info" role="status">
            <ShieldCheck size={16} />
            <div>
              <strong>草稿已创建，尚未写入事实账本</strong>
              <p>请再次核对。确认后只建立期初组合基线，不会伪造买入或出入金。</p>
            </div>
          </div>
          <dl className="detail-list">
            <div><dt>标的</dt><dd>{draft.item.name}（{draft.item.symbol}）</dd></div>
            <div><dt>类型</dt><dd>{draft.item.assetType}</dd></div>
            <div><dt>数量 / 份额</dt><dd className="mono">{draft.item.quantity}</dd></div>
            <div><dt>单位成本</dt><dd className="mono">{draft.item.unitCost}</dd></div>
            <div><dt>基线日期</dt><dd className="mono">{draft.item.tradeDate}</dd></div>
          </dl>
          {error ? <div className="alert alert-danger" role="alert">{error}</div> : null}
          <div className="dialog-actions">
            <button className="btn-secondary" type="button" disabled={submitting} onClick={onClose}>
              稍后处理
            </button>
            <button className="btn-primary" type="button" disabled={submitting} onClick={() => void handleConfirm()}>
              {confirmMutation.isPending ? <Loader2 className="spin" size={14} /> : <CheckCircle2 size={14} />}
              确认期初持仓
            </button>
          </div>
        </div>
      ) : (
        <form className="dialog-content" onSubmit={handleCreate}>
          <div className="alert alert-info">
            <ShieldCheck size={16} />
            <div>
              <strong>这是期初组合初始化，不是成交补录</strong>
              <p>提交后先生成草稿预览；只有再次确认才会建立期初 ADJUSTMENT 基线。</p>
            </div>
          </div>
          <div className="field">
            <label htmlFor="position-search">搜索标的（代码或名称）</label>
            {selected ? (
              <div className="security-cell selected-security">
                <div>
                  <strong>{selected.name}</strong>
                  <span className="mono">{selected.symbol} · {selected.market}</span>
                </div>
                <button className="icon-button" type="button" aria-label="重新选择标的" onClick={() => setSelected(null)}>
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
                    placeholder="至少输入 2 个字符，例如 600519 / 茅台"
                    aria-controls="position-search-results"
                    aria-autocomplete="list"
                    onChange={(event) => setSearchQuery(event.target.value)}
                    autoFocus
                  />
                </label>
                {searchQuery.trim().length > 0 && searchQuery.trim().length < 2 ? (
                  <span className="help-text">请至少输入 2 个字符，避免触发无效全市场查询</span>
                ) : null}
                {debouncedQuery.length >= 2 ? (
                  <div className="search-results" id="position-search-results" role="listbox" aria-label="标的搜索结果">
                    {search.isLoading ? (
                      <div className="search-result-hint"><Loader2 className="spin" size={13} /> 搜索中…</div>
                    ) : search.error ? (
                      <div className="search-result-hint">搜索服务暂不可用，请稍后重试</div>
                    ) : (search.data ?? []).length === 0 ? (
                      <div className="search-result-hint">未找到匹配标的</div>
                    ) : (
                      (search.data ?? []).map((item) => (
                        <button
                          type="button"
                          role="option"
                          aria-selected="false"
                          className="search-result-row"
                          key={`${item.market}-${item.symbol}-${item.type}`}
                          onClick={() => pickInstrument(item)}
                        >
                          <span className="security-cell"><strong>{item.name}</strong><span className="mono">{item.symbol}</span></span>
                          <span className="tag">{item.type}</span>
                        </button>
                      ))
                    )}
                  </div>
                ) : null}
              </>
            )}
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="position-quantity">数量 / 份额 *</label>
              <input id="position-quantity" className="input" inputMode="decimal" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="position-avg-cost">成本价（每股/每份）*</label>
              <input id="position-avg-cost" className="input" inputMode="decimal" value={avgCost} onChange={(event) => setAvgCost(event.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="position-buy-date">期初基线日期</label>
              <input id="position-buy-date" className="input" type="date" value={baselineDate} max={todayLocalIso()} onChange={(event) => setBaselineDate(event.target.value)} />
            </div>
          </div>
          {error ? <div className="alert alert-danger" role="alert">{error}</div> : null}
          <div className="dialog-actions">
            <button className="btn-secondary" type="button" disabled={submitting} onClick={onClose}>取消</button>
            <button className="btn-primary" type="submit" disabled={submitting || !formValid}>
              {submitting ? <Loader2 className="spin" size={14} /> : <Plus size={14} />}
              创建草稿并预览
            </button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

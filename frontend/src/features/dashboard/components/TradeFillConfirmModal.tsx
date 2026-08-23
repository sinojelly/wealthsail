import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Check,
  Database,
  ShieldCheck,
} from "lucide-react";
import type { SettlementPreviewDto } from "../../../api/generated/types";
import type { FillDraft } from "../../../api/portfolio";
import Dialog from "../../../components/accessibility/Dialog";
import { formatMoney, formatSignedMoney } from "../../../domain/money";

interface Props {
  open: boolean;
  fills: FillDraft[];
  previews: Map<string, SettlementPreviewDto>;
  onClose: () => void;
  onConfirm: (id: string) => void;
  onInvalidateAll: () => void;
}

export default function TradeFillConfirmModal({
  open,
  fills,
  previews,
  onClose,
  onConfirm,
  onInvalidateAll,
}: Props) {
  return (
    <Dialog open={open} title="成交草稿校对确认" size="xl" onClose={onClose}>
      <div className="dialog-content fill-dialog">
        <div className="fill-summary">
          <div>
            <span>待确认</span>
            <strong className="mono">{fills.length} 笔待确认</strong>
          </div>
          <div>
            <span>确认前可用现金</span>
            <strong className="mono">¥201,382.15</strong>
          </div>
          <div>
            <span>入账语义</span>
            <strong>原事件不可修改 · 支持冲正</strong>
          </div>
        </div>
        <div className="alert alert-warning">
          <AlertTriangle size={16} />
          <div>
            <strong>这是成交事实校对，不是下单界面</strong>
            <p>
              请核对方向、数量、价格、费用和资金影响。确认后若发现错账，请创建冲正事件，系统保留完整审计链。
            </p>
          </div>
        </div>
        <div className="fill-list">
          {fills.map((fill) => {
            const preview = previews.get(fill.id);
            return (
              <article className="fill-card" key={fill.id}>
                <div className="fill-card-main">
                  <div
                    className={`side-icon ${fill.side === "BUY" ? "buy" : "sell"}`}
                  >
                    {fill.side === "BUY" ? (
                      <ArrowDownRight size={18} />
                    ) : (
                      <ArrowUpRight size={18} />
                    )}
                  </div>
                  <div className="fill-security">
                    <span
                      className={`side-label ${fill.side === "BUY" ? "buy" : "sell"}`}
                    >
                      {fill.side === "BUY" ? "买入" : "卖出"}
                    </span>
                    <strong>{fill.name}</strong>
                    <small className="mono">
                      {fill.symbol} · {fill.id}
                    </small>
                  </div>
                  <dl className="fill-numbers">
                    <div>
                      <dt>数量</dt>
                      <dd className="mono">{fill.quantity}</dd>
                    </div>
                    <div>
                      <dt>成交价</dt>
                      <dd className="mono">¥{fill.price}</dd>
                    </div>
                    <div>
                      <dt>费用</dt>
                      <dd className="mono">¥{fill.fee}</dd>
                    </div>
                    <div>
                      <dt>时间</dt>
                      <dd className="mono">{fill.occurredAt}</dd>
                    </div>
                  </dl>
                  <button
                    className="btn-primary"
                    aria-label={`确认${fill.name}成交`}
                    onClick={() => onConfirm(fill.id)}
                  >
                    <Check size={14} />
                    确认入账
                  </button>
                </div>
                <div className="settlement-preview">
                  <span>
                    <Database size={12} />
                    {fill.source}
                  </span>
                  {preview ? (
                    <>
                      <span>
                        资金变动{" "}
                        <strong
                          className={`mono ${preview.cashDelta.startsWith("-") ? "negative" : "positive"}`}
                        >
                          {formatSignedMoney(preview.cashDelta)}
                        </strong>
                      </span>
                      <span>
                        预计余额{" "}
                        <strong className="mono">
                          {formatMoney(preview.cashAfter)}
                        </strong>
                      </span>
                    </>
                  ) : (
                    <span>正在计算资金影响…</span>
                  )}
                </div>
              </article>
            );
          })}
        </div>
        {fills.length === 0 ? (
          <div className="all-confirmed">
            <ShieldCheck size={26} />
            <strong>本批成交草稿已全部处理</strong>
            <span>所有确认均已写入审计事件流。</span>
          </div>
        ) : null}
      </div>
      <footer className="dialog-footer">
        <button
          className="btn-danger"
          disabled={fills.length === 0}
          onClick={onInvalidateAll}
        >
          全部作废
        </button>
        <button className="btn-secondary" onClick={onClose}>
          完成校对
        </button>
      </footer>
    </Dialog>
  );
}

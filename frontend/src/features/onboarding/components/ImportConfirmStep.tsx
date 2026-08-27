import type { TiantianImportDetail } from "../../../api/types";

interface Props {
  detail: TiantianImportDetail;
  confirmEmpty: boolean;
  reviewed: boolean;
  pending: boolean;
  stale: boolean;
  error: string | null;
  onConfirmEmpty: (value: boolean) => void;
  onReviewed: (value: boolean) => void;
  onConfirm: () => void;
  onCancel: () => void;
  onRefresh: () => void;
}

export default function ImportConfirmStep({
  detail,
  confirmEmpty,
  reviewed,
  pending,
  stale,
  error,
  onConfirmEmpty,
  onReviewed,
  onConfirm,
  onCancel,
  onRefresh,
}: Props) {
  const allClosed =
    detail.positions.length > 0 &&
    detail.positions.every((item) => item.comparisonStatus === "CLOSED");
  return (
    <footer className="tiantian-confirm-step">
      <div className="confirmation-boundary">
        <strong>确认后会发生什么</strong>
        <ul>
          <li>
            当前快照按基金逐项更新来源账户基线，并保留可审计的旧基线撤销记录；
          </li>
          <li>历史流水只保存为来源事实，不会伪造成真实买卖或出入金；</li>
          <li>活期宝和非入账记录保持排除，官方净值区间指标由后台继续计算。</li>
        </ul>
      </div>
      <label className="review-confirmation">
        <input
          type="checkbox"
          checked={reviewed}
          onChange={(event) => onReviewed(event.target.checked)}
        />
        我已核对当前持仓，并确认使用该快照更新天天基金账户
      </label>
      {allClosed ? (
        <label className="danger-confirmation">
          <input
            type="checkbox"
            checked={confirmEmpty}
            onChange={(event) => onConfirmEmpty(event.target.checked)}
          />
          我确认本次快照会清空该账户的全部基金持仓
        </label>
      ) : null}
      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}
      <div className="dialog-actions">
        {stale ? (
          <button className="btn-secondary" type="button" onClick={onRefresh}>
            刷新预览
          </button>
        ) : null}
        <button
          className="btn-secondary"
          type="button"
          onClick={onCancel}
          disabled={pending}
        >
          取消草稿
        </button>
        <button
          className="btn-primary"
          type="button"
          onClick={onConfirm}
          disabled={
            !detail.canConfirm ||
            !reviewed ||
            pending ||
            (allClosed && !confirmEmpty)
          }
        >
          {pending ? "确认中…" : "确认写入持仓基线"}
        </button>
      </div>
    </footer>
  );
}

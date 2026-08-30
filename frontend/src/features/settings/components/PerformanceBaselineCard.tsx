import { useState, type FormEvent } from "react";
import { CircleDollarSign, Save } from "lucide-react";
import {
  usePerformanceBaselines,
  useRevisePerformanceBaseline,
} from "../../../api/hooks";
import type { PerformanceBaselineDto } from "../../../api/types";

function BaselineEditor({
  baseline,
  canEdit,
}: {
  baseline: PerformanceBaselineDto;
  canEdit: boolean;
}) {
  const mutation = useRevisePerformanceBaseline();
  const [value, setValue] = useState(baseline.initialCumulativePnl);
  const [message, setMessage] = useState<string | null>(null);
  const normalized = value.trim();
  const unchanged = normalized === baseline.initialCumulativePnl;
  const valid = normalized !== "" && Number.isFinite(Number(normalized));

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    try {
      const result = await mutation.mutateAsync({
        accountId: baseline.accountId,
        initialCumulativePnl: normalized,
        version: baseline.version,
      });
      setValue(result.initialCumulativePnl);
      setMessage("历史累计收益已更新");
    } catch {
      // The mutation error stays visible below and the entered value is kept.
    }
  }

  const inputId = `performance-baseline-${baseline.accountId}`;
  return (
    <form
      className="performance-baseline-row"
      onSubmit={(event) => void handleSubmit(event)}
    >
      <div className="performance-baseline-account">
        <strong>{baseline.accountName}</strong>
        <span>从 {baseline.baselineAsOf} 起自动跟踪</span>
        <small>
          起点资产 ¥{baseline.baselineTotalAsset} · 当前版本 {baseline.version}
        </small>
      </div>
      <div className="field performance-baseline-field">
        <label htmlFor={inputId}>
          跟踪前历史累计收益（{baseline.accountName}）
        </label>
        <div className="performance-baseline-controls">
          <input
            id={inputId}
            className="input mono"
            type="number"
            step="0.01"
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              setMessage(null);
            }}
            disabled={!canEdit || mutation.isPending}
          />
          <button
            type="submit"
            className="btn-primary btn-sm"
            disabled={!canEdit || !valid || unchanged || mutation.isPending}
          >
            <Save size={14} />
            {mutation.isPending ? "保存中…" : "保存历史累计收益"}
          </button>
        </div>
        {mutation.error instanceof Error ? (
          <p className="field-error" role="alert">
            {mutation.error.message}
          </p>
        ) : null}
        {message ? (
          <p className="form-ok" role="status">
            {message}
          </p>
        ) : null}
      </div>
    </form>
  );
}

export default function PerformanceBaselineCard({
  canEdit,
}: {
  canEdit: boolean;
}) {
  const baselines = usePerformanceBaselines();

  return (
    <section className="panel performance-baseline-card">
      <div className="panel-header">
        <div>
          <h2>累计收益起点（可选）</h2>
          <p>系统会自动跟踪后续收益；只有明确知道旧平台历史收益时才需要填写</p>
        </div>
        <CircleDollarSign size={17} />
      </div>
      <div className="performance-baseline-note">
        默认历史累计收益为 0，不影响当前持仓收益。累计收益 = 历史累计收益 +
        跟踪以来资产变化 − 净入金；买卖不会被当作新增本金。
      </div>
      {baselines.isLoading ? (
        <p className="panel-empty-copy">正在加载收益起点…</p>
      ) : baselines.error instanceof Error ? (
        <p className="field-error" role="alert">
          {baselines.error.message}
        </p>
      ) : baselines.data?.length ? (
        <div className="performance-baseline-list">
          {baselines.data.map((baseline) => (
            <BaselineEditor
              key={`${baseline.accountId}:${baseline.version}`}
              baseline={baseline}
              canEdit={canEdit}
            />
          ))}
        </div>
      ) : (
        <p className="panel-empty-copy">
          完成首个完整估值后，系统会自动建立收益跟踪起点，无需手工操作。
        </p>
      )}
    </section>
  );
}

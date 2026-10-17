import { useState } from "react";
import { sourceHref, timeLabel } from "../macro-display";

export interface StudyPreview {
  title: string;
  thesis: string;
  asOf?: string | null;
  watchFor: string[];
  counterFactors: string[];
  supports?: string[];
  sources: Array<{
    id: string;
    title?: string;
    url?: string;
    verification?: string;
  }>;
  selection?: boolean;
}

export default function StudyCreateConfirmation({
  preview,
  pending = false,
  error,
  onCancel,
  onSave,
}: {
  preview: StudyPreview;
  pending?: boolean;
  error?: string;
  onCancel: () => void;
  onSave: (horizonDays: number) => void;
}) {
  const [days, setDays] = useState("30");
  const [invalid, setInvalid] = useState(false);
  return (
    <section className="op-study-confirmation" aria-label="确认研究假设">
      <h4>确认研究假设 · {preview.title}</h4>
      <details open>
        <summary>原始研究假设与依据</summary>
        <p>{preview.thesis}</p>
        <small>
          {preview.selection
            ? "观察依据时间：保存时逐项记录各事实的实际日期。"
            : `观察依据时间：${timeLabel(preview.asOf)}`}
        </small>
        <p>
          下一步验证：{preview.watchFor.join("；") || "尚未声明，需人工核查。"}
        </p>
        {!!preview.supports?.length && (
          <p>当前支持依据：{preview.supports.join("；")}</p>
        )}
        <p>
          反方条件：
          {preview.counterFactors.join("；") || "尚未声明，需人工核查。"}
        </p>
        <p>自动失效条件尚未声明；反方情景不等于已配置的自动规则。</p>
        {preview.sources.map((source) => (
          <p key={source.id}>
            {sourceHref(source.url) ? (
              <a href={sourceHref(source.url)} target="_blank" rel="noreferrer">
                {source.title || "原始来源"}
              </a>
            ) : (
              source.title || "原始来源待核查"
            )}{" "}
            ·{" "}
            {source.verification === "SOURCE_CHECKED"
              ? "引用原文已核对，影响判断仍待验证"
              : "来源与事实待核查"}
          </p>
        ))}
        {!preview.sources.length && (
          <p>
            {preview.selection
              ? "保存时重新读取当前标的事实与来源，最终以服务器冻结版本为准。"
              : "尚无已列出的来源，保存不代表事实已核实。"}
          </p>
        )}
      </details>
      <form
        aria-label="确认研究跟踪"
        onSubmit={(event) => {
          event.preventDefault();
          const horizon = Number(days);
          if (!Number.isInteger(horizon) || horizon < 1 || horizon > 365) {
            setInvalid(true);
            return;
          }
          setInvalid(false);
          if (!pending) onSave(horizon);
        }}
      >
        <label>
          观察天数
          <input
            type="number"
            min="1"
            max="365"
            step="1"
            required
            value={days}
            disabled={pending}
            onChange={(event) => setDays(event.target.value)}
          />
        </label>
        <small>
          默认30天，可按研究需要调整1—365天；这是复盘窗口，不是收益预测或模型推荐期限。
        </small>
        <small>
          同一来源已跟踪时会打开已有主题，保留原观察期和版本；调整请在主题内修订。
        </small>
        {invalid && <p role="alert">请输入1—365之间的整数天数。</p>}
        {error && <p role="alert">{error}</p>}
        <div className="op-research-actions">
          <button type="submit" disabled={pending}>
            {pending ? "保存中…" : "确认并保存跟踪"}
          </button>
          <button type="button" disabled={pending} onClick={onCancel}>
            取消跟踪确认
          </button>
        </div>
      </form>
    </section>
  );
}

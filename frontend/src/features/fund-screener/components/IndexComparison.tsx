import { Check } from "lucide-react";
import type { ResearchIndex } from "../../../api/investment";
export default function IndexComparison({
  items,
  selectedId,
  onSelect,
  loading,
  error,
}: {
  items: ResearchIndex[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  loading: boolean;
  error: boolean;
}) {
  return (
    <section className="op-index-list">
      <div className="op-section-label">
        <strong>指数选择</strong>
        <span>{items.length} 个候选 · 趋势与风险分别核验</span>
      </div>
      {loading ? (
        <p role="status" className="op-empty">
          指数加载中…
        </p>
      ) : error ? (
        <p role="alert" className="op-empty">
          指数读取失败
        </p>
      ) : items.length ? (
        items.map((i) => (
          <button
            key={i.id}
            aria-label={`${i.name} ${i.id === selectedId ? "当前指数" : "查看"}`}
            aria-pressed={i.id === selectedId}
            className={i.id === selectedId ? "op-selected" : ""}
            onClick={() => onSelect(i.id)}
          >
            <span>
              <strong>
                {i.name} {i.id === selectedId && <Check size={14} />}
              </strong>
              <small>
                {i.code} · {i.exposure}
              </small>
            </span>
            <span>{i.id === selectedId ? "当前指数" : "查看 →"}</span>
          </button>
        ))
      ) : (
        <p className="op-empty">
          该方向尚未建立指数覆盖，请切换方向或使用全局搜索。
        </p>
      )}
    </section>
  );
}

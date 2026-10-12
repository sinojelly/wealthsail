import { useState } from "react";
import type { Direction } from "../../../api/investment";
export default function DirectionExplorer({
  directions,
  selected,
  onSelect,
}: {
  directions: Direction[];
  selected?: Direction;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const markets = [
    ...new Map(directions.map((d) => [d.marketId, d.marketLabel])).entries(),
  ];
  return (
    <section className="op-panel">
      <nav className="op-direction-nav" aria-label="市场与方向">
        <div>
          <span className="op-muted">市场</span>
          {markets.map(([id, label]) => (
            <button
              key={id}
              aria-pressed={selected?.marketId === id}
              className={selected?.marketId === id ? "op-market-active" : ""}
              onClick={() =>
                onSelect(directions.find((d) => d.marketId === id)!.id)
              }
            >
              {label}
            </button>
          ))}
        </div>
        <div>
          <span className="op-muted">方向</span>
          {directions
            .filter((d) => d.marketId === selected?.marketId)
            .map((d) => (
              <button
                key={d.id}
                aria-pressed={selected?.id === d.id}
                className={selected?.id === d.id ? "op-direction-active" : ""}
                onClick={() => onSelect(d.id)}
              >
                {d.label}
              </button>
            ))}
        </div>
      </nav>
      <div className="op-direction-caption">
        <strong>
          当前研究：
          {selected
            ? `${selected.marketLabel} · ${selected.label}`
            : "未确认方向"}
        </strong>
        <span>
          {selected?.framework ?? "可以直接搜索任意基金，未知归属不自动推断。"}
        </span>
        <button aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "收起依据" : "查看驱动与风险"}⌄
        </button>
      </div>
      {open && (
        <p className="op-evidence">
          研究框架用于组织信息，支持与反方因素尚未自动评估。指数趋势、基金质量和组合风险分别核验。
        </p>
      )}
    </section>
  );
}

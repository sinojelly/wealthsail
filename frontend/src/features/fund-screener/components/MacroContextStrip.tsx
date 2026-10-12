import { useState } from "react";
import { Globe2, ChevronDown } from "lucide-react";
import type { MacroContext } from "../../../api/investment";
export default function MacroContextStrip({
  data,
  error,
}: {
  data?: MacroContext;
  error: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="op-panel">
      <button
        className="op-macro-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span>
          <Globe2 size={19} />
          <strong>市场环境概览</strong>
          <span className="op-muted">增长 · 通胀 · 流动性 · 风险</span>
        </span>
        <span>
          <span className="op-badge">
            {error
              ? "读取失败"
              : data?.state === "READY"
                ? "事实已更新"
                : "数据待核验"}
          </span>
          <ChevronDown size={16} />
        </span>
      </button>
      {open && (
        <div className="op-macro-grid">
          {data?.items.length ? (
            data.items.map((i) => (
              <article key={i.code}>
                <strong>{i.label}</strong>
                <h3>{i.value ?? "—"}</h3>
                <p>
                  观察期：{i.asOf ?? "未知"} · {i.state}
                </p>
                <p>
                  公布：{i.publishedAt ?? "未核实"} · 来源：{i.source ?? "未知"}
                </p>
              </article>
            ))
          ) : (
            <p>宏观数据尚未接入。方向探索和基金搜索仍可使用。</p>
          )}
        </div>
      )}
    </section>
  );
}

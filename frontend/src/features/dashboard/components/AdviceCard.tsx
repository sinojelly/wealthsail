import { RefreshCw, ShieldAlert, Sparkles } from "lucide-react";
import type { AiAdvice } from "../../../api/hooks";

function actionClass(action: string): string {
  if (action === "减仓" || action === "加仓") return "status-pill active";
  if (action === "关注") return "status-pill pending";
  return "status-pill";
}

function riskClass(risk: string): string {
  if (risk === "高") return "risk-high";
  if (risk === "中") return "risk-mid";
  return "risk-low";
}

export default function AdviceCard({
  advice,
  loading,
  refreshing,
  onRefresh,
}: {
  advice?: AiAdvice;
  loading?: boolean;
  refreshing?: boolean;
  onRefresh?: () => void;
}) {
  const items = advice?.items ?? [];
  return (
    <section className="panel advice-card">
      <div className="panel-header">
        <div>
          <h2>AI 操作建议</h2>
          <p>基于当前持仓、研报与市场行情生成 · 仅供操作参考</p>
        </div>
        <div className="panel-header-actions">
          <button
            className="btn-secondary"
            disabled={loading || refreshing}
            onClick={() => onRefresh?.()}
            title="重新生成建议"
          >
            <RefreshCw size={13} className={refreshing ? "spin" : ""} />
            重新生成
          </button>
          <Sparkles size={17} />
        </div>
      </div>
      <div className="panel-body">
        {loading ? (
          <div className="binding-loader" />
        ) : items.length === 0 ? (
          <div className="binding-empty">
            <div className="binding-hero-icon">
              <Sparkles size={22} />
            </div>
            <strong>暂无可用建议</strong>
            <p>需要持仓、研报或行情数据才能生成；请先完成初始化导入。</p>
          </div>
        ) : (
          <>
            <ul className="advice-list">
              {items.map((item, index) => (
                <li className="advice-item" key={`${item.title}-${index}`}>
                  <span className={actionClass(item.action)}>{item.action}</span>
                  <div className="advice-copy">
                    <div className="advice-title">
                      <strong>{item.title}</strong>
                      <span className={`risk-tag ${riskClass(item.riskLevel)}`}>
                        风险 {item.riskLevel}
                      </span>
                    </div>
                    <p>{item.detail}</p>
                  </div>
                </li>
              ))}
            </ul>
            <p className="advice-disclaimer">
              <ShieldAlert size={13} />
              {advice?.disclaimer ?? ""}
              {advice?.generatedAt
                ? `（生成于 ${new Date(advice.generatedAt).toLocaleString("zh-CN", { hour12: false })}）`
                : ""}
            </p>
          </>
        )}
      </div>
    </section>
  );
}

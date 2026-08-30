import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import {
  initialRiskBudget,
  riskSchema,
  type RiskBudgetValues,
} from "../risk-budget-schema";

export default function RiskBudgetForm({
  onPreview,
  currentPolicy,
}: {
  onPreview: (values: RiskBudgetValues) => Promise<void>;
  currentPolicy: { version: number; effectiveFrom: string } | null;
}) {
  const {
    register,
    handleSubmit,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<RiskBudgetValues>({
    defaultValues: initialRiskBudget,
    resolver: zodResolver(riskSchema),
    mode: "onBlur",
  });
  return (
    <form className="risk-form" onSubmit={handleSubmit(onPreview)}>
      {isDirty ? (
        <div className="draft-banner">
          <span className="status-pill draft">DRAFT</span>
          <p>当前修改尚未生效。请先预览影响，再完成重新认证与确认。</p>
        </div>
      ) : currentPolicy ? (
        <div className="confirmed-banner">
          <span className="status-pill confirmed">CONFIRMED</span>
          <p>
            当前生效版本 · v{currentPolicy.version} · {currentPolicy.effectiveFrom} 起
          </p>
        </div>
      ) : (
        <div className="unconfigured-banner">
          <span className="status-pill unconfigured">未配置</span>
          <p>尚未配置生效策略</p>
        </div>
      )}
      <section className="risk-form-section">
        <header>
          <div>
            <span>01</span>
            <div>
              <h2>投资目标</h2>
              <p>用于报告基准与评估周期，不承诺收益。</p>
            </div>
          </div>
        </header>
        <div className="risk-fields">
          <div className="field">
            <label htmlFor="benchmark">业绩比较基准</label>
            <select
              id="benchmark"
              className="select"
              {...register("benchmark")}
            >
              <option value="CSI300">沪深300（000300）</option>
              <option value="ZZ500">中证500（000905）</option>
              <option value="CUSTOM">自定义组合基准</option>
            </select>
            <span className="help-text">
              所有收益归因同时展示绝对收益与相对基准。
            </span>
          </div>
          <div className="field">
            <label htmlFor="horizonMonths">评估周期（月）</label>
            <input
              id="horizonMonths"
              className="input mono"
              type="number"
              {...register("horizonMonths")}
            />
            <span className="help-text">短期波动不会自动改变长期目标。</span>
          </div>
        </div>
      </section>
      <section className="risk-form-section">
        <header>
          <div>
            <span>02</span>
            <div>
              <h2>风险预算</h2>
              <p>所有比例作为字符串提交，由服务端执行确定性 Decimal 计算。</p>
            </div>
          </div>
        </header>
        <div className="risk-fields three">
          <div className="field">
            <label htmlFor="maxPosition">单一标的最大仓位</label>
            <div className="suffix-input">
              <input
                id="maxPosition"
                aria-label="单一标的最大仓位"
                className="input mono"
                inputMode="decimal"
                {...register("maxPosition")}
              />
              <span>%</span>
            </div>
            {errors.maxPosition ? (
              <em className="field-error">{errors.maxPosition.message}</em>
            ) : (
              <span className="help-text">超限只告警，不自动卖出。</span>
            )}
          </div>
          <div className="field">
            <label htmlFor="maxDrawdown">组合最大回撤预算</label>
            <div className="suffix-input">
              <input
                id="maxDrawdown"
                className="input mono"
                inputMode="decimal"
                {...register("maxDrawdown")}
              />
              <span>%</span>
            </div>
            {errors.maxDrawdown ? (
              <em className="field-error">{errors.maxDrawdown.message}</em>
            ) : (
              <span className="help-text">触达后升级风险提示。</span>
            )}
          </div>
          <div className="field">
            <label htmlFor="minCash">最低现金比例</label>
            <div className="suffix-input">
              <input
                id="minCash"
                className="input mono"
                inputMode="decimal"
                {...register("minCash")}
              />
              <span>%</span>
            </div>
            {errors.minCash ? (
              <em className="field-error">{errors.minCash.message}</em>
            ) : (
              <span className="help-text">作为流动性缓冲线。</span>
            )}
          </div>
        </div>
      </section>
      <section className="risk-form-section">
        <header>
          <div>
            <span>03</span>
            <div>
              <h2>提醒节流</h2>
              <p>降低噪声，保留风险升级通道。</p>
            </div>
          </div>
        </header>
        <div className="risk-fields">
          <div className="field">
            <label htmlFor="maxAlertsPerDay">单日常规提醒上限</label>
            <input
              id="maxAlertsPerDay"
              className="input mono"
              type="number"
              {...register("maxAlertsPerDay")}
            />
            <span className="help-text">高风险事件不受常规上限限制。</span>
          </div>
          <div className="field">
            <label htmlFor="silenceMinutes">同类事件静默期（分钟）</label>
            <input
              id="silenceMinutes"
              className="input mono"
              type="number"
              {...register("silenceMinutes")}
            />
            <span className="help-text">事件升级时可突破静默期。</span>
          </div>
        </div>
      </section>
      <footer className="risk-form-actions">
        <span>
          {isDirty
            ? "有未保存修改"
            : currentPolicy
              ? "当前配置与已确认版本一致"
              : "填写或调整参数后可预览并确认"}
        </span>
        <button
          type="submit"
          className="btn-primary"
          disabled={!isDirty || isSubmitting}
        >
          {isSubmitting ? "正在计算…" : "预览策略影响"}
        </button>
      </footer>
    </form>
  );
}

import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  FileCheck2,
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";
import type { RiskPreviewDto } from "../../api/generated/types";
import { previewRiskPolicy } from "../../api/risk";
import Dialog from "../../components/accessibility/Dialog";
import PageHeader from "../../components/layout/PageHeader";
import RiskBudgetForm from "./components/RiskBudgetForm";
import type { RiskBudgetValues } from "./risk-budget-schema";

export default function RiskPolicyPage() {
  const [preview, setPreview] = useState<RiskPreviewDto | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reauth, setReauth] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [policyVersion, setPolicyVersion] = useState(3);

  async function handlePreview(values: RiskBudgetValues) {
    setConfirmed(false);
    setPreview(await previewRiskPolicy(values.maxPosition));
  }

  function confirmPolicy() {
    if (!reauth.trim()) return;
    setConfirmOpen(false);
    setPreview(null);
    setConfirmed(true);
    setPolicyVersion((version) => version + 1);
    setReauth("");
  }

  return (
    <>
      <PageHeader
        title="风控政策"
        description="风险规则的草稿、预览、确认与版本审计 · 不连接自动交易"
        actions={
          <button className="btn-secondary">
            <Clock3 size={14} />
            查看历史版本
          </button>
        }
      />
      {confirmed ? (
        <div className="alert risk-success">
          <CheckCircle2 size={16} />
          <div>
            <strong>策略已确认并启用</strong>
            <p>新策略已生成不可变版本，后续报告将引用新的 policy_hash。</p>
          </div>
        </div>
      ) : null}
      <div className="risk-layout">
        <div className="panel">
          <RiskBudgetForm key={policyVersion} onPreview={handlePreview} />
        </div>
        <aside className="risk-aside">
          <section className="panel policy-state">
            <div className="panel-header">
              <h2>当前生效政策</h2>
              <span className="status-pill confirmed">生效中</span>
            </div>
            <div className="panel-body">
              <dl>
                <div>
                  <dt>版本</dt>
                  <dd className="mono">v{policyVersion}</dd>
                </div>
                <div>
                  <dt>确认人</dt>
                  <dd>李明远 · OWNER</dd>
                </div>
                <div>
                  <dt>确认时间</dt>
                  <dd className="mono">
                    {confirmed ? "2026-08-23 23:07" : "2026-07-15 09:26"}
                  </dd>
                </div>
                <div>
                  <dt>策略哈希</dt>
                  <dd className="mono">
                    {confirmed ? "sha256:demo-2500" : "sha256:4e78…01a9"}
                  </dd>
                </div>
              </dl>
            </div>
          </section>
          <section className="panel guardrail-card">
            <div className="panel-header">
              <h2>能力边界</h2>
            </div>
            <div className="panel-body">
              <div>
                <ShieldCheck size={16} />
                <p>
                  <strong>只读分析</strong>
                  <span>规则仅影响提醒、报告和草稿校验。</span>
                </p>
              </div>
              <div>
                <LockKeyhole size={16} />
                <p>
                  <strong>高风险操作需重新认证</strong>
                  <span>确认会写入 actor、tenant、policy hash。</span>
                </p>
              </div>
              <div className="disabled-capability">
                <AlertTriangle size={16} />
                <p>
                  <strong>自动下单 · L4</strong>
                  <span>当前阶段禁用，优先级置于最后。</span>
                </p>
              </div>
            </div>
          </section>
        </aside>
      </div>
      {preview ? (
        <section className="preview-panel panel" aria-live="polite">
          <div className="preview-heading">
            <div>
              <FileCheck2 size={19} />
              <div>
                <h2>策略变更预览</h2>
                <p>此结果由策略服务按确定性规则计算，确认后才生效。</p>
              </div>
            </div>
            <span className="status-pill previewing">PREVIEW</span>
          </div>
          <div className="preview-grid">
            <div>
              <span>单标的最大仓位</span>
              <strong className="mono">
                {preview.previousMaxPosition}% → {preview.nextMaxPosition}%
              </strong>
            </div>
            <div>
              <span>受影响持仓</span>
              <strong className="mono">{preview.impactedHoldings} 个</strong>
            </div>
            <div>
              <span>新增提醒</span>
              <strong className="mono">{preview.newAlerts} 条</strong>
            </div>
            <div>
              <span>候选策略哈希</span>
              <strong className="mono">{preview.policyHash}</strong>
            </div>
          </div>
          <div className="preview-actions">
            <button className="btn-secondary" onClick={() => setPreview(null)}>
              放弃预览
            </button>
            <button
              className="btn-primary"
              onClick={() => setConfirmOpen(true)}
            >
              确认并启用
            </button>
          </div>
        </section>
      ) : null}
      <Dialog
        open={confirmOpen}
        title="确认高风险策略变更"
        size="md"
        onClose={() => setConfirmOpen(false)}
      >
        <div className="dialog-content">
          <div className="alert alert-danger">
            <LockKeyhole size={16} />
            <div>
              <strong>需要重新验证身份</strong>
              <p>
                启用后会改变后续风险判定。原版本不会删除，可通过新版本回退。
              </p>
            </div>
          </div>
          <div className="field">
            <label htmlFor="reauth">重新验证口令</label>
            <input
              id="reauth"
              aria-label="重新验证口令"
              type="password"
              className="input"
              value={reauth}
              onChange={(event) => setReauth(event.target.value)}
              autoComplete="current-password"
            />
            <span className="help-text">
              演示环境接受任意非空口令；TARGET 接入服务端二次认证挑战。
            </span>
          </div>
        </div>
        <footer className="dialog-footer">
          <button
            className="btn-secondary"
            onClick={() => setConfirmOpen(false)}
          >
            取消
          </button>
          <button
            className="btn-primary"
            disabled={!reauth.trim()}
            onClick={confirmPolicy}
          >
            确认启用策略
          </button>
        </footer>
      </Dialog>
    </>
  );
}

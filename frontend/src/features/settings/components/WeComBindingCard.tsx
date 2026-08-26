import { useEffect, useState } from "react";
import {
  Check,
  Clipboard,
  Clock3,
  KeyRound,
  MessageSquare,
  RefreshCw,
  ShieldOff,
} from "lucide-react";
import {
  useCreateBindingCredential,
  useRevokeBinding,
} from "../../../api/hooks";
import type { MeBinding } from "../../../api/types";
import Dialog from "../../../components/accessibility/Dialog";

type BindingState = "idle" | "generating" | "pending" | "active" | "revoked";

export default function WeComBindingCard({
  bindings,
  onBound,
}: {
  bindings: MeBinding[];
  onBound?: () => void;
}) {
  const existing = bindings.find((binding) => binding.status === "ACTIVE");
  const create = useCreateBindingCredential();
  const revoke = useRevokeBinding();
  const [state, setState] = useState<BindingState>(existing ? "active" : "idle");
  const [token, setToken] = useState("");
  const [copied, setCopied] = useState(false);
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [reauth, setReauth] = useState("");
  const displayState: BindingState =
    state === "revoked"
      ? "revoked"
      : existing
        ? "active"
        : state === "active"
          ? "idle"
          : state;

  // Only the verified WeCom callback can activate a binding. While waiting,
  // periodically refresh /me; the browser never supplies an external user id.
  useEffect(() => {
    if (displayState !== "pending" || !onBound) return;
    const timer = window.setInterval(onBound, 5_000);
    return () => window.clearInterval(timer);
  }, [displayState, onBound]);

  const busy = create.isPending || revoke.isPending;
  const bindingError =
    create.error instanceof Error
      ? create.error.message
      : revoke.error instanceof Error
        ? revoke.error.message
        : null;

  async function generate() {
    setState("generating");
    try {
      const credential = await create.mutateAsync();
      setToken(credential.token);
      setState("pending");
    } catch {
      setState("idle");
    }
  }

  async function copyCommand() {
    const command = `/bind ${token}`;
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(command);
    setCopied(true);
  }

  async function confirmRevoke() {
    if (!existing || !reauth) return;
    try {
      await revoke.mutateAsync({ bindingId: existing.id, password: reauth });
      setRevokeOpen(false);
      setReauth("");
      setState("revoked");
      onBound?.();
    } catch {
      // mutation error remains visible in the card and dialog stays open
    }
  }

  return (
    <>
      <section className="panel wecom-card">
        <div className="panel-header">
          <div>
            <h2>企业微信绑定</h2>
            <p>把盘中触点、报告通知与用户回复关联到当前租户</p>
          </div>
          <MessageSquare size={17} />
        </div>
        <div className="panel-body">
          {displayState === "idle" || displayState === "revoked" ? (
            <div className="binding-empty">
              <div className="binding-hero-icon">
                {displayState === "revoked" ? <ShieldOff size={25} /> : <MessageSquare size={25} />}
              </div>
              <strong>{displayState === "revoked" ? "已撤销" : "尚未绑定企业微信"}</strong>
              <p>
                {displayState === "revoked"
                  ? "旧绑定保留审计记录，但已不能作为消息路由目标。"
                  : "生成 15 分钟有效的一次性凭证，再从企业微信向已配置的应用发送绑定命令。"}
              </p>
              {bindingError ? <div className="alert alert-danger">{bindingError}</div> : null}
              <button className="btn-primary" disabled={busy} onClick={() => void generate()}>
                {displayState === "revoked" ? <RefreshCw size={14} /> : <KeyRound size={14} />}
                {displayState === "revoked" ? "重新生成绑定凭证" : "生成一次性绑定凭证"}
              </button>
            </div>
          ) : null}
          {displayState === "generating" ? (
            <div className="binding-empty">
              <div className="binding-loader" />
              <strong>正在生成安全凭证</strong>
              <p>凭证仅保存带密钥摘要，明文只在当前页面短暂展示。</p>
            </div>
          ) : null}
          {displayState === "pending" ? (
            <div className="binding-pending">
              <div className="pending-heading">
                <div>
                  <span className="status-pill pending"><Clock3 size={10} />等待企业微信回调确认</span>
                  <strong>请在凭证过期前完成绑定</strong>
                </div>
              </div>
              <div className="command-box">
                <span>向 WealthSail 企业微信应用发送</span>
                <code>/bind {token}</code>
                <button className="btn-secondary" onClick={() => void copyCommand()}>
                  {copied ? <Check size={13} /> : <Clipboard size={13} />}
                  {copied ? "已复制" : "复制命令"}
                </button>
                <small>页面会轮询绑定状态；只有签名、时效和 CorpID 校验通过的回调才能激活。</small>
              </div>
              <div className="credential-note">
                <KeyRound size={14} />
                <p>
                  <strong>一次性凭证规则</strong>
                  <span>单次使用、限时有效、明文不落库；重复或跨租户消费会被拒绝并记审计。</span>
                </p>
              </div>
            </div>
          ) : null}
          {displayState === "active" && existing ? (
            <div className="binding-active">
              <div className="active-mark"><Check size={23} /></div>
              <div className="active-copy">
                <span className="status-pill active">已绑定</span>
                <h3>WealthSail · {existing.externalUsername ?? "已验证员工"}</h3>
                <dl>
                  <div><dt>渠道</dt><dd className="mono">{existing.channelType}</dd></div>
                  <div><dt>绑定时间</dt><dd className="mono">{existing.boundAt ? existing.boundAt.replace("T", " ").slice(0, 16) : "—"}</dd></div>
                  <div><dt>最近活动</dt><dd className="mono">{existing.lastUsedAt ? existing.lastUsedAt.replace("T", " ").slice(0, 16) : "—"}</dd></div>
                </dl>
              </div>
              <button className="btn-danger" onClick={() => setRevokeOpen(true)}>解除绑定</button>
            </div>
          ) : null}
        </div>
      </section>
      <Dialog open={revokeOpen} title="解除企业微信绑定" size="md" onClose={() => setRevokeOpen(false)}>
        <div className="dialog-content">
          <div className="alert alert-danger">
            <ShieldOff size={16} />
            <div>
              <strong>解除后立即停止消息路由</strong>
              <p>历史消息、决策事件和审计日志不会删除；重新绑定会创建新的 binding_id。</p>
            </div>
          </div>
          <div className="field">
            <label htmlFor="binding-reauth">重新验证口令</label>
            <input id="binding-reauth" aria-label="解除绑定重新验证口令" type="password" className="input" value={reauth} onChange={(event) => setReauth(event.target.value)} autoComplete="current-password" />
          </div>
          {bindingError ? <div className="alert alert-danger">{bindingError}</div> : null}
        </div>
        <footer className="dialog-footer">
          <button className="btn-secondary" onClick={() => setRevokeOpen(false)}>取消</button>
          <button className="btn-danger" disabled={!reauth || busy} onClick={() => void confirmRevoke()}>确认解除绑定</button>
        </footer>
      </Dialog>
    </>
  );
}

import { useState } from "react";
import {
  Check,
  Clipboard,
  Clock3,
  KeyRound,
  MessageSquare,
  QrCode,
  RefreshCw,
  ShieldOff,
} from "lucide-react";
import {
  useConsumeBindingCredential,
  useCreateBindingCredential,
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
  const consume = useConsumeBindingCredential();
  const [state, setState] = useState<BindingState>(
    existing ? "active" : "idle",
  );
  const [token, setToken] = useState("");
  const [copied, setCopied] = useState(false);
  const [revokeOpen, setRevokeOpen] = useState(false);

  const busy = create.isPending || consume.isPending;
  const bindingError =
    create.error instanceof Error
      ? create.error.message
      : consume.error instanceof Error
        ? consume.error.message
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
    if (navigator.clipboard?.writeText)
      await navigator.clipboard.writeText(command);
    setCopied(true);
  }

  /** 模拟企业微信确认：消费一次性口令并绑定当前用户。 */
  async function simulateConfirm() {
    try {
      await consume.mutateAsync({
        token,
        externalUserId: `demo_wecom_${Date.now() % 100000}`,
        externalUsername: "演示企微员工",
      });
      setState("active");
      onBound?.();
    } catch {
      // error 由 bindingError 展示
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
          {state === "idle" || state === "revoked" ? (
            <div className="binding-empty">
              <div className="binding-hero-icon">
                {state === "revoked" ? (
                  <ShieldOff size={25} />
                ) : (
                  <MessageSquare size={25} />
                )}
              </div>
              <strong>
                {state === "revoked" ? "已撤销" : "尚未绑定企业微信"}
              </strong>
              <p>
                {state === "revoked"
                  ? "旧凭证与旧绑定关系仍保留审计记录，但已不能接收或发送消息。"
                  : "生成 15 分钟有效的一次性凭证，在企业微信中完成员工身份确认。"}
              </p>
              {bindingError ? (
                <div className="alert alert-danger">{bindingError}</div>
              ) : null}
              <button className="btn-primary" onClick={generate}>
                {state === "revoked" ? (
                  <RefreshCw size={14} />
                ) : (
                  <KeyRound size={14} />
                )}
                {state === "revoked"
                  ? "重新生成绑定凭证"
                  : "生成一次性绑定凭证"}
              </button>
            </div>
          ) : null}
          {state === "generating" ? (
            <div className="binding-empty">
              <div className="binding-loader" />
              <strong>正在生成安全凭证</strong>
              <p>凭证仅保存哈希，明文只在当前页面短暂展示。</p>
            </div>
          ) : null}
          {state === "pending" ? (
            <div className="binding-pending">
              <div className="pending-heading">
                <div>
                  <span className="status-pill pending">
                    <Clock3 size={10} />
                    等待员工确认
                  </span>
                  <strong>请在 15 分钟内完成绑定</strong>
                </div>
                <div className="countdown mono">15:00</div>
              </div>
              <div className="binding-methods">
                <div className="qr-placeholder">
                  <QrCode size={68} />
                  <span>企业微信扫码</span>
                  <small>二维码仅绑定当前租户与用户</small>
                </div>
                <div className="command-box">
                  <span>或向机器人发送命令</span>
                  <code>/bind {token}</code>
                  <button className="btn-secondary" onClick={copyCommand}>
                    {copied ? <Check size={13} /> : <Clipboard size={13} />}
                    {copied ? "已复制" : "复制命令"}
                  </button>
                  <button
                    className="btn-primary simulate-button"
                    disabled={busy}
                    onClick={simulateConfirm}
                  >
                    {busy ? "绑定中…" : "模拟企业微信确认"}
                  </button>
                </div>
              </div>
              <div className="credential-note">
                <KeyRound size={14} />
                <p>
                  <strong>一次性凭证规则</strong>
                  <span>
                    单次使用、15
                    分钟过期、仅保存哈希；重复使用与跨租户使用都会被拒绝并记审计。
                  </span>
                </p>
              </div>
            </div>
          ) : null}
          {state === "active" ? (
            <div className="binding-active">
              <div className="active-mark">
                <Check size={23} />
              </div>
              <div className="active-copy">
                <span className="status-pill active">已绑定</span>
                <h3>WealthSail · {existing?.externalUsername ?? "已绑定"}</h3>
                <dl>
                  <div>
                    <dt>渠道</dt>
                    <dd className="mono">
                      {existing?.channelType ?? "wecom"}
                    </dd>
                  </div>
                  <div>
                    <dt>绑定时间</dt>
                    <dd className="mono">
                      {existing?.boundAt
                        ? existing.boundAt.replace("T", " ").slice(0, 16)
                        : "—"}
                    </dd>
                  </div>
                  <div>
                    <dt>最近活动</dt>
                    <dd className="mono">
                      {existing?.lastUsedAt
                        ? existing.lastUsedAt.replace("T", " ").slice(0, 16)
                        : "—"}
                    </dd>
                  </div>
                </dl>
              </div>
              <button
                className="btn-danger"
                onClick={() => setRevokeOpen(true)}
              >
                解除绑定
              </button>
            </div>
          ) : null}
        </div>
      </section>
      <Dialog
        open={revokeOpen}
        title="解除企业微信绑定"
        size="md"
        onClose={() => setRevokeOpen(false)}
      >
        <div className="dialog-content">
          <div className="alert alert-danger">
            <ShieldOff size={16} />
            <div>
              <strong>解除后立即停止消息路由</strong>
              <p>
                历史消息、决策事件和审计日志不会删除。重新绑定将创建新的
                binding_id。
              </p>
            </div>
          </div>
          <p className="dialog-copy">
            确认解除当前企业微信员工与当前租户的绑定关系？
          </p>
        </div>
        <footer className="dialog-footer">
          <button
            className="btn-secondary"
            onClick={() => setRevokeOpen(false)}
          >
            取消
          </button>
          <button
            className="btn-danger"
            onClick={() => {
              setRevokeOpen(false);
              setState("revoked");
              onBound?.();
            }}
          >
            确认解除绑定
          </button>
        </footer>
      </Dialog>
    </>
  );
}

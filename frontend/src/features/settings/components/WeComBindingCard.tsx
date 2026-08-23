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
import { createBindingCredential } from "../../../api/identity";
import Dialog from "../../../components/accessibility/Dialog";

type BindingState = "idle" | "generating" | "pending" | "active" | "revoked";

export default function WeComBindingCard() {
  const [state, setState] = useState<BindingState>("idle");
  const [token, setToken] = useState("");
  const [copied, setCopied] = useState(false);
  const [revokeOpen, setRevokeOpen] = useState(false);

  async function generate() {
    setState("generating");
    const credential = await createBindingCredential();
    setToken(credential.token);
    setState("pending");
  }

  async function copyCommand() {
    const command = `/bind ${token}`;
    if (navigator.clipboard?.writeText)
      await navigator.clipboard.writeText(command);
    setCopied(true);
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
                  <strong>请在 15:00 内完成绑定</strong>
                </div>
                <div className="countdown mono">14:59</div>
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
                    onClick={() => setState("active")}
                  >
                    模拟企业微信确认
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
                <h3>WealthSail · 李明远</h3>
                <dl>
                  <div>
                    <dt>企业微信员工</dt>
                    <dd className="mono">zhangsan.demo</dd>
                  </div>
                  <div>
                    <dt>绑定时间</dt>
                    <dd className="mono">2026-08-23 23:09</dd>
                  </div>
                  <div>
                    <dt>最近消息</dt>
                    <dd className="mono">刚刚 · BINDING_CONFIRMED</dd>
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
            确认解除当前企业微信员工与“明远家庭账户”的绑定关系？
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
            }}
          >
            确认解除绑定
          </button>
        </footer>
      </Dialog>
    </>
  );
}

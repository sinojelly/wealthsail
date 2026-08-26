import { useState, type FormEvent } from "react";
import {
  Check,
  Clipboard,
  KeyRound,
  MailPlus,
  ShieldAlert,
  UserPlus,
} from "lucide-react";
import { useCreateInvitation } from "../../../api/hooks";
import type {
  InvitationCredential,
  InvitableTenantRole,
  TenantRole,
} from "../../../api/types";

const ROLE_OPTIONS: Array<{
  value: InvitableTenantRole;
  label: string;
  description: string;
}> = [
  { value: "VIEWER", label: "只读成员", description: "可查看资产、报告和风险结果" },
  { value: "EDITOR", label: "编辑成员", description: "可维护持仓和业务数据" },
  { value: "ADMIN", label: "管理员", description: "可邀请成员并访问管理能力" },
];

export default function InvitationCard({
  activeRole,
  tenantName,
}: {
  activeRole: TenantRole;
  tenantName: string;
}) {
  const createInvitation = useCreateInvitation();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<InvitableTenantRole>("VIEWER");
  const [invitation, setInvitation] = useState<InvitationCredential | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);

  if (activeRole !== "OWNER" && activeRole !== "ADMIN") return null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCopied(false);
    setCopyError(false);
    try {
      const result = await createInvitation.mutateAsync({
        email: email.trim().toLowerCase(),
        role,
      });
      setInvitation(result);
    } catch {
      // Mutation error is rendered below; a failed request must not leave an old token visible.
      setInvitation(null);
    }
  }

  async function copyToken() {
    if (!invitation) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(invitation.token);
      setCopied(true);
      setCopyError(false);
    } catch {
      setCopied(false);
      setCopyError(true);
    }
  }

  return (
    <section className="panel invitation-card">
      <div className="panel-header">
        <div>
          <h2>邀请成员</h2>
          <p>为 {tenantName || "当前租户"} 创建限邮箱、限角色的一次性邀请码</p>
        </div>
        <UserPlus size={17} />
      </div>
      <div className="panel-body invitation-body">
        <form className="invitation-form" onSubmit={(event) => void submit(event)}>
          <div className="field">
            <label htmlFor="invitation-email">受邀邮箱</label>
            <input
              id="invitation-email"
              className="input"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="name@example.com"
              autoComplete="email"
              required
            />
          </div>
          <div className="field">
            <label htmlFor="invitation-role">成员角色</label>
            <select
              id="invitation-role"
              className="input"
              value={role}
              onChange={(event) => setRole(event.target.value as InvitableTenantRole)}
            >
              {ROLE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}（{option.value}）
                </option>
              ))}
            </select>
          </div>
          <div className="invitation-role-note">
            <ShieldAlert size={14} />
            <span>{ROLE_OPTIONS.find((option) => option.value === role)?.description}</span>
          </div>
          {role === "ADMIN" ? (
            <div className="alert alert-warning invitation-admin-warning">
              <ShieldAlert size={16} />
              <div>
                <strong>管理员拥有成员邀请和管理权限</strong>
                <p>只应授予可信人员；受邀人完成注册后立即获得当前租户的 ADMIN 角色。</p>
              </div>
            </div>
          ) : null}
          {createInvitation.error instanceof Error ? (
            <p className="field-error" role="alert">{createInvitation.error.message}</p>
          ) : null}
          <button
            className="btn-primary invitation-submit"
            type="submit"
            disabled={createInvitation.isPending || !email.trim()}
          >
            <MailPlus size={14} />
            {createInvitation.isPending ? "生成中…" : "生成邀请码"}
          </button>
        </form>

        {invitation ? (
          <div className="invitation-result" aria-live="polite">
            <div className="invitation-result-heading">
              <span className="invitation-result-icon"><KeyRound size={18} /></span>
              <div>
                <strong>邀请码只显示一次</strong>
                <p>请通过可信渠道发送给 {invitation.email}，不要截图留存在公共群聊。</p>
              </div>
            </div>
            <code aria-label="一次性邀请码">{invitation.token}</code>
            <dl>
              <div><dt>角色</dt><dd>{invitation.role}</dd></div>
              <div><dt>有效期至</dt><dd>{invitation.expiresAt.replace("T", " ").slice(0, 16)}</dd></div>
            </dl>
            <button className="btn-secondary" type="button" onClick={() => void copyToken()}>
              {copied ? <Check size={14} /> : <Clipboard size={14} />}
              {copied ? "已复制" : "复制邀请码"}
            </button>
            {copyError ? <small className="field-error">浏览器无法访问剪贴板，请手动复制。</small> : null}
          </div>
        ) : null}
      </div>
    </section>
  );
}

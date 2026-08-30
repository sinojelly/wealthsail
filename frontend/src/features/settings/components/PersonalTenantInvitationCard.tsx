import { useState, type FormEvent } from "react";
import { Building2, Check, Clipboard, KeyRound, ShieldCheck } from "lucide-react";
import { useCreatePersonalTenantInvitation } from "../../../api/hooks";
import type { PersonalTenantInvitationCredential } from "../../../api/types";

export default function PersonalTenantInvitationCard() {
  const createInvitation = useCreatePersonalTenantInvitation();
  const [email, setEmail] = useState("");
  const [tenantName, setTenantName] = useState("");
  const [invitation, setInvitation] =
    useState<PersonalTenantInvitationCredential | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setInvitation(null);
    setCopied(false);
    setCopyError(false);
    try {
      setInvitation(
        await createInvitation.mutateAsync({
          email: email.trim().toLowerCase(),
          tenantName: tenantName.trim(),
        }),
      );
    } catch {
      // Mutation error is rendered below. Never retain a token from an earlier request.
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

  const formValid = email.trim().includes("@") && tenantName.trim().length >= 2;

  return (
    <section className="panel invitation-card">
      <div className="panel-header">
        <div>
          <h2>创建独立租户</h2>
          <p>平台治理能力 · 受邀人注册后成为其个人租户的唯一 OWNER</p>
        </div>
        <Building2 size={17} />
      </div>
      <div className="panel-body invitation-body">
        <form className="invitation-form" onSubmit={(event) => void submit(event)}>
          <div className="field">
            <label htmlFor="personal-tenant-email">独立租户受邀邮箱</label>
            <input
              id="personal-tenant-email"
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
            <label htmlFor="personal-tenant-name">个人租户名称</label>
            <input
              id="personal-tenant-name"
              className="input"
              value={tenantName}
              onChange={(event) => setTenantName(event.target.value)}
              minLength={2}
              maxLength={64}
              placeholder="例如：Jelly 的个人投资账户"
              required
            />
          </div>
          <div className="invitation-role-note">
            <ShieldCheck size={14} />
            <span>该邀请不会加入当前租户；注册、建租户和授予 OWNER 在同一事务完成。</span>
          </div>
          {createInvitation.error instanceof Error ? (
            <p className="field-error" role="alert">{createInvitation.error.message}</p>
          ) : null}
          <button
            className="btn-primary invitation-submit"
            type="submit"
            disabled={createInvitation.isPending || !formValid}
          >
            <Building2 size={14} />
            {createInvitation.isPending ? "创建中…" : "创建独立租户邀请码"}
          </button>
        </form>

        {invitation ? (
          <div className="invitation-result" aria-live="polite">
            <div className="invitation-result-heading">
              <span className="invitation-result-icon"><KeyRound size={18} /></span>
              <div>
                <strong>独立租户邀请码只显示一次</strong>
                <p>{invitation.email} 注册后将创建“{invitation.tenantName}”。</p>
              </div>
            </div>
            <code aria-label="独立租户一次性邀请码">{invitation.token}</code>
            <dl>
              <div><dt>租户角色</dt><dd>OWNER</dd></div>
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

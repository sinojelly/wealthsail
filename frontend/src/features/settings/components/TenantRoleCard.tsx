import { useState, type FormEvent } from "react";
import { Crown, Eye, Save, ShieldCheck, UserRound, Users } from "lucide-react";
import { useUpdateTenantName } from "../../../api/hooks";
import type { MeTenant, TenantRole } from "../../../api/types";
import {
  switchTenant,
  updateSessionTenantName,
} from "../../../app/auth-session";

const ROLE_ICON: Record<string, typeof Crown> = {
  OWNER: Crown,
  ADMIN: ShieldCheck,
  EDITOR: Users,
  VIEWER: Eye,
};

export default function TenantRoleCard({
  tenants,
  activeTenantId,
  activeRole,
  onChanged,
}: {
  tenants: MeTenant[];
  activeTenantId: string;
  activeRole: TenantRole;
  onChanged: () => void;
}) {
  const activeTenant =
    tenants.find((tenant) => tenant.tenantId === activeTenantId) ?? tenants[0];
  const [pendingTenantId, setPendingTenantId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tenantName, setTenantName] = useState(activeTenant?.name ?? "");
  const [renameMessage, setRenameMessage] = useState<string | null>(null);
  const updateTenantName = useUpdateTenantName();

  async function handleSwitch(tenantId: string) {
    setPendingTenantId(tenantId);
    setError(null);
    try {
      await switchTenant(tenantId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "租户切换失败");
    } finally {
      setPendingTenantId(null);
    }
  }

  async function handleRename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = tenantName.trim();
    setRenameMessage(null);
    try {
      const result = await updateTenantName.mutateAsync({ name });
      setTenantName(result.name);
      updateSessionTenantName(result.name);
      onChanged();
      setRenameMessage("租户名称已更新");
    } catch {
      // Mutation error is displayed below; retain the user's input for correction/retry.
    }
  }

  const normalizedName = tenantName.trim();
  const canRename =
    activeRole === "OWNER" &&
    normalizedName.length >= 2 &&
    normalizedName.length <= 64 &&
    normalizedName !== activeTenant?.name;

  return (
    <section className="panel tenant-card">
      <div className="panel-header">
        <div>
          <h2>租户与角色</h2>
          <p>当前用户的活动租户与角色边界</p>
        </div>
        <span className="status-pill tenant-pill">
          {tenantName || activeTenant?.name || "—"}
        </span>
      </div>
      {activeRole === "OWNER" ? (
        <form
          className="tenant-name-form"
          onSubmit={(event) => void handleRename(event)}
        >
          <div className="field">
            <label htmlFor="active-tenant-name">租户名称</label>
            <input
              id="active-tenant-name"
              className="input"
              value={tenantName}
              minLength={2}
              maxLength={64}
              onChange={(event) => {
                setTenantName(event.target.value);
                setRenameMessage(null);
              }}
              aria-describedby="active-tenant-name-hint"
              required
            />
            <small id="active-tenant-name-hint">
              2–64 个字符；只修改显示名称，不改变成员、账户或资产。
            </small>
          </div>
          <button
            className="btn-primary btn-sm tenant-name-submit"
            type="submit"
            disabled={!canRename || updateTenantName.isPending}
          >
            <Save size={14} />
            {updateTenantName.isPending ? "保存中…" : "保存租户名称"}
          </button>
          {updateTenantName.error instanceof Error ? (
            <p className="field-error" role="alert">
              {updateTenantName.error.message}
            </p>
          ) : null}
          {renameMessage ? (
            <p className="form-ok" role="status">
              {renameMessage}
            </p>
          ) : null}
        </form>
      ) : null}
      <div className="member-list">
        {tenants.map((tenant) => {
          const Icon = ROLE_ICON[tenant.role] ?? UserRound;
          const isActive = tenant.tenantId === activeTenantId;
          return (
            <div className="member-row" key={tenant.tenantId}>
              <div className="member-avatar">
                <Icon size={15} />
              </div>
              <div>
                <strong>
                  {isActive ? tenantName || tenant.name : tenant.name}
                </strong>
                <span className="mono">{tenant.tenantId.slice(0, 8)}</span>
              </div>
              <span className="role-chip">{tenant.role}</span>
              {isActive ? (
                <small>当前租户</small>
              ) : tenant.status === "ACTIVE" ? (
                <button
                  className="btn-ghost"
                  disabled={pendingTenantId !== null}
                  onClick={() => void handleSwitch(tenant.tenantId)}
                >
                  {pendingTenantId === tenant.tenantId ? "切换中…" : "切换"}
                </button>
              ) : (
                <small>{tenant.status}</small>
              )}
            </div>
          );
        })}
        {tenants.length === 0 ? (
          <div className="member-row">
            <div className="member-avatar">
              <UserRound size={15} />
            </div>
            <div>
              <strong>暂无租户成员关系</strong>
            </div>
            <span className="role-chip">—</span>
            <small>—</small>
          </div>
        ) : null}
      </div>
      {error ? (
        <p className="field-error" role="alert">
          {error}
        </p>
      ) : null}
      <footer className="tenant-footer">
        <ShieldCheck size={14} />
        <span>
          当前角色 {activeRole}；所有查询和写入均携带 tenant_id，后端 RLS
          再次鉴权。
        </span>
      </footer>
    </section>
  );
}

import { Crown, Eye, ShieldCheck, UserRound, Users } from "lucide-react";
import type { MeTenant } from "../../../api/types";

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
}: {
  tenants: MeTenant[];
  activeTenantId: string;
  activeRole: string;
}) {
  const activeTenant =
    tenants.find((tenant) => tenant.tenantId === activeTenantId) ?? tenants[0];

  return (
    <section className="panel tenant-card">
      <div className="panel-header">
        <div>
          <h2>租户与角色</h2>
          <p>当前用户的活动租户与角色边界</p>
        </div>
        <span className="status-pill tenant-pill">
          {activeTenant?.name ?? "—"}
        </span>
      </div>
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
                <strong>{tenant.name}</strong>
                <span className="mono">{tenant.tenantId.slice(0, 8)}</span>
              </div>
              <span className="role-chip">{tenant.role}</span>
              <small>{isActive ? "当前租户" : tenant.status}</small>
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

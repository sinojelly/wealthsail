import { Crown, Eye, ShieldCheck, Users } from "lucide-react";

const members = [
  {
    name: "李明远",
    identity: "limingyuan",
    role: "OWNER",
    state: "当前用户",
    icon: Crown,
  },
  {
    name: "王晓雯",
    identity: "wangxiaowen",
    role: "MEMBER",
    state: "已启用",
    icon: Users,
  },
  {
    name: "家庭观察员",
    identity: "viewer-01",
    role: "READ_ONLY",
    state: "已启用",
    icon: Eye,
  },
];

export default function TenantRoleCard() {
  return (
    <section className="panel tenant-card">
      <div className="panel-header">
        <div>
          <h2>租户与角色</h2>
          <p>2–3 人 MVP 的最小权限边界</p>
        </div>
        <span className="status-pill tenant-pill">明远家庭账户</span>
      </div>
      <div className="member-list">
        {members.map(({ name, identity, role, state, icon: Icon }) => (
          <div className="member-row" key={identity}>
            <div className="member-avatar">
              <Icon size={15} />
            </div>
            <div>
              <strong>{name}</strong>
              <span className="mono">{identity}</span>
            </div>
            <span className="role-chip">{role}</span>
            <small>{state}</small>
          </div>
        ))}
      </div>
      <footer className="tenant-footer">
        <ShieldCheck size={14} />
        <span>
          所有查询和写入均携带
          tenant_id；前端角色只用于体验控制，后端必须再次鉴权。
        </span>
      </footer>
    </section>
  );
}

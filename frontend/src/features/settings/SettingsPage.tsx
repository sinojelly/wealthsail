import {
  Fingerprint,
  KeyRound,
  LockKeyhole,
  ShieldCheck,
  Smartphone,
  UserCheck,
} from "lucide-react";
import PageHeader from "../../components/layout/PageHeader";
import TenantRoleCard from "./components/TenantRoleCard";
import WeComBindingCard from "./components/WeComBindingCard";

export default function SettingsPage() {
  return (
    <>
      <PageHeader
        title="账号安全"
        description="用户、租户角色、企业微信身份与高风险操作认证"
      />
      <div className="settings-grid">
        <div className="settings-main">
          <WeComBindingCard />
          <TenantRoleCard />
        </div>
        <aside className="settings-aside">
          <section className="panel security-card">
            <div className="panel-header">
              <div>
                <h2>安全状态</h2>
                <p>当前用户的认证保护</p>
              </div>
              <ShieldCheck size={17} />
            </div>
            <div className="security-list">
              <div>
                <span className="security-icon good">
                  <LockKeyhole size={15} />
                </span>
                <p>
                  <strong>登录口令</strong>
                  <small>最近更新：28 天前</small>
                </p>
                <span className="status-pill active">正常</span>
              </div>
              <div>
                <span className="security-icon good">
                  <Fingerprint size={15} />
                </span>
                <p>
                  <strong>高风险操作二次认证</strong>
                  <small>策略确认、绑定撤销、权限变更</small>
                </p>
                <span className="status-pill active">已启用</span>
              </div>
              <div>
                <span className="security-icon">
                  <Smartphone size={15} />
                </span>
                <p>
                  <strong>可信设备</strong>
                  <small>2 台设备 · 最近活动于本机</small>
                </p>
                <button className="btn-ghost">管理</button>
              </div>
            </div>
          </section>
          <section className="panel session-card">
            <div className="panel-header">
              <h2>当前会话</h2>
            </div>
            <div className="panel-body">
              <dl>
                <div>
                  <dt>
                    <UserCheck size={12} />
                    用户
                  </dt>
                  <dd>李明远</dd>
                </div>
                <div>
                  <dt>
                    <KeyRound size={12} />
                    访问令牌
                  </dt>
                  <dd>仅内存 · 不落盘</dd>
                </div>
                <div>
                  <dt>租户</dt>
                  <dd className="mono">tenant-demo-001</dd>
                </div>
                <div>
                  <dt>时区</dt>
                  <dd className="mono">Asia/Shanghai</dd>
                </div>
              </dl>
            </div>
          </section>
          <div className="alert alert-info">
            <ShieldCheck size={16} />
            <div>
              <strong>认证架构说明</strong>
              <p>
                Access Token 仅保存在内存；Refresh Token 使用 Secure + HttpOnly
                + SameSite Cookie，前端代码不可读取。
              </p>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}

import {
  AlertTriangle,
  Fingerprint,
  KeyRound,
  LockKeyhole,
  ShieldCheck,
  Smartphone,
  UserCheck,
} from "lucide-react";
import { useMe } from "../../api/hooks";
import PageHeader from "../../components/layout/PageHeader";
import ProfileCard from "./components/ProfileCard";
import TenantRoleCard from "./components/TenantRoleCard";
import WeComBindingCard from "./components/WeComBindingCard";

export default function SettingsPage() {
  const me = useMe();
  const data = me.data;

  return (
    <>
      <PageHeader
        title="账号安全"
        description="用户、租户角色、企业微信身份与高风险操作认证"
      />
      {me.error instanceof Error ? (
        <div className="alert alert-danger" role="alert">
          <AlertTriangle size={16} />
          <div><strong>账号信息加载失败</strong><p>{me.error.message}</p></div>
        </div>
      ) : null}
      <div className="settings-grid">
        <div className="settings-main">
          {data ? (
            <ProfileCard profile={data.profile} onChanged={() => me.refetch()} />
          ) : null}
          <WeComBindingCard
            bindings={data?.bindings ?? []}
            onBound={() => me.refetch()}
          />
          <TenantRoleCard
            tenants={data?.tenants ?? []}
            activeTenantId={data?.activeTenantId ?? ""}
            activeRole={data?.activeRole ?? "VIEWER"}
          />
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
                  <small>服务端验证 · 密码哈希不离开认证边界</small>
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
                  <small>由后端设备管理接管（未接入）</small>
                </p>
                <button className="btn-ghost" disabled title="可信设备管理尚未接入">管理</button>
              </div>
            </div>
          </section>
          <section className="panel session-card">
            <div className="panel-header">
              <h2>当前会话</h2>
            </div>
            <div className="panel-body">
              {me.isLoading ? (
                <p className="muted">加载中…</p>
              ) : data ? (
                <dl>
                  <div>
                    <dt>
                      <UserCheck size={12} />
                      用户
                    </dt>
                    <dd>{data.profile.nickname}</dd>
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
                    <dd className="mono">{data.activeTenantId.slice(0, 8)}</dd>
                  </div>
                  <div>
                    <dt>角色</dt>
                    <dd className="mono">{data.activeRole}</dd>
                  </div>
                  <div>
                    <dt>时区</dt>
                    <dd className="mono">Asia/Shanghai</dd>
                  </div>
                </dl>
              ) : (
                <p className="muted">会话信息不可用</p>
              )}
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

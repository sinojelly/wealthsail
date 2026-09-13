import { useState } from "react";
import {
  AlertTriangle,
  Bell,
  Building2,
  ChevronRight,
  Fingerprint,
  KeyRound,
  LockKeyhole,
  MessageSquare,
  ShieldCheck,
  Smartphone,
  UserCheck,
  UserCog,
} from "lucide-react";
import { useMe } from "../../api/hooks";
import PageHeader from "../../components/layout/PageHeader";
import ProfileCard from "./components/ProfileCard";
import InvitationCard from "./components/InvitationCard";
import PersonalTenantInvitationCard from "./components/PersonalTenantInvitationCard";
import TenantRoleCard from "./components/TenantRoleCard";
import WeComBindingCard from "./components/WeComBindingCard";
import PerformanceBaselineCard from "./components/PerformanceBaselineCard";
import ReportSubscriptionCard from "./components/ReportSubscriptionCard";

type CategoryKey = "profile" | "security" | "subscribe" | "tenant" | "notify";

const CATEGORIES: {
  key: CategoryKey;
  label: string;
  hint: string;
  icon: typeof UserCog;
}[] = [
  { key: "profile", label: "个人资料", hint: "昵称与头像", icon: UserCog },
  { key: "security", label: "安全设置", hint: "认证与会话", icon: ShieldCheck },
  { key: "subscribe", label: "通知订阅", hint: "研报推送开关", icon: Bell },
  { key: "tenant", label: "租户与成员", hint: "角色与邀请", icon: Building2 },
  { key: "notify", label: "消息通知", hint: "企业微信绑定", icon: MessageSquare },
];

export default function SettingsPage() {
  const me = useMe();
  const data = me.data;
  const [category, setCategory] = useState<CategoryKey>("profile");
  const boundCount = data?.bindings.length ?? 0;

  return (
    <>
      <PageHeader
        title="个人中心"
        description="账号资料、安全认证、通知订阅与租户成员管理"
      />
      {me.error instanceof Error ? (
        <div className="alert alert-danger" role="alert">
          <AlertTriangle size={16} />
          <div>
            <strong>账号信息加载失败</strong>
            <p>{me.error.message}</p>
          </div>
        </div>
      ) : null}

      {data ? (
        <section className="panel account-hero">
          <div className="account-hero-avatar">
            {data.profile.avatarUrl ? (
              <img src={data.profile.avatarUrl} alt={data.profile.nickname} />
            ) : (
              <span>{data.profile.nickname.slice(0, 1) || "?"}</span>
            )}
          </div>
          <div className="account-hero-meta">
            <strong>{data.profile.nickname}</strong>
            <div>
              <span>
                绑定状态：
                {boundCount > 0
                  ? `已绑定 ${boundCount} 个企业微信渠道`
                  : "未绑定企业微信"}
              </span>
              <span>邮箱：{data.profile.email ?? "未设置"}</span>
            </div>
          </div>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => setCategory("profile")}
          >
            修改资料
          </button>
        </section>
      ) : null}

      <div className="account-layout">
        <nav className="account-nav" aria-label="个人中心功能导航">
          {CATEGORIES.map((item) => {
            const Icon = item.icon;
            const isActive = category === item.key;
            return (
              <button
                key={item.key}
                type="button"
                className={`account-nav-item ${isActive ? "is-active" : ""}`}
                onClick={() => setCategory(item.key)}
                aria-current={isActive ? "true" : undefined}
              >
                <span className="account-nav-icon">
                  <Icon size={16} />
                </span>
                <span className="account-nav-text">
                  <strong>{item.label}</strong>
                  <small>{item.hint}</small>
                </span>
                <ChevronRight size={15} className="account-nav-chevron" />
              </button>
            );
          })}
        </nav>

        <div className="account-detail">
          {category === "profile" ? (
            data ? (
              <ProfileCard profile={data.profile} onChanged={() => me.refetch()} />
            ) : (
              <p className="muted">加载中…</p>
            )
          ) : null}

          {category === "security" ? (
            <>
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
                    <button
                      className="btn-ghost"
                      disabled
                      title="可信设备管理尚未接入"
                    >
                      管理
                    </button>
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
                        <dd className="mono">
                          {data.activeTenantId.slice(0, 8)}
                        </dd>
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
                    Access Token 仅保存在内存；Refresh Token 使用 Secure +
                    HttpOnly + SameSite Cookie，前端代码不可读取。
                  </p>
                </div>
              </div>
            </>
          ) : null}

          {category === "subscribe" ? (
            data ? (
              <ReportSubscriptionCard
                key={`${data.activeTenantId}:${Object.values(
                  data.reportSubscriptions,
                ).join(":")}`}
                subscriptions={data.reportSubscriptions}
              />
            ) : (
              <p className="muted">加载中…</p>
            )
          ) : null}

          {category === "notify" ? (
            <WeComBindingCard
              bindings={data?.bindings ?? []}
              onBound={() => me.refetch()}
            />
          ) : null}

          {category === "tenant" ? (
            <>
              <TenantRoleCard
                key={data?.activeTenantId ?? "tenant-role-loading"}
                tenants={data?.tenants ?? []}
                activeTenantId={data?.activeTenantId ?? ""}
                activeRole={data?.activeRole ?? "VIEWER"}
                onChanged={() => me.refetch()}
              />
              <PerformanceBaselineCard
                canEdit={data?.activeRole !== "VIEWER"}
              />
              <InvitationCard
                activeRole={data?.activeRole ?? "VIEWER"}
                tenantName={
                  data?.tenants.find(
                    (tenant) => tenant.tenantId === data.activeTenantId,
                  )?.name ?? ""
                }
              />
              {data?.platformRoles.includes("PLATFORM_ADMIN") ? (
                <PersonalTenantInvitationCard />
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}

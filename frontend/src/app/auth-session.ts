export type TenantRole = "OWNER" | "MEMBER" | "READ_ONLY" | "PLATFORM_ADMIN";

export interface AuthSession {
  userId: string;
  displayName: string;
  tenantId: string;
  tenantName: string;
  role: TenantRole;
}

// TARGET: 会话由后端 access token（仅内存）与 HttpOnly refresh cookie 建立。
// 当前 fixture 只用于在后端接入前验证多租户 UI 和权限边界。
export const fixtureSession: AuthSession = {
  userId: "usr-owner-001",
  displayName: "李明远",
  tenantId: "tenant-demo-001",
  tenantName: "明远家庭账户",
  role: "OWNER",
};

export function canViewOperations(session: AuthSession): boolean {
  return session.role === "OWNER" || session.role === "PLATFORM_ADMIN";
}

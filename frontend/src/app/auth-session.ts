import { useSyncExternalStore } from "react";
import { configureAuthLifecycle, setAccessToken } from "../api/client";
import * as identityApi from "../api/identity";
import type { LoginResponse, PlatformRole, TenantRole } from "../api/types";
import { queryClient } from "./query-client";

export interface AuthSession {
  userId: string;
  displayName: string;
  email: string | null;
  tenantId: string;
  tenantName: string;
  role: TenantRole;
  platformRoles: PlatformRole[];
  accessToken: string;
  expiresIn: number;
}

let currentSession: AuthSession | null = null;
let bootstrapPromise: Promise<AuthSession | null> | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function getSession(): AuthSession | null {
  return currentSession;
}

export function getSessionSubject(session: AuthSession | null): string {
  return session ? `${session.tenantId}:${session.userId}` : "anonymous";
}

export function privateQueryKey(
  session: AuthSession | null,
  domain: string,
  ...parts: ReadonlyArray<string | number>
) {
  return ["v1", getSessionSubject(session), domain, ...parts] as const;
}

async function clearPrivateSessionState() {
  await queryClient.cancelQueries();
  queryClient.clear();
}

function setSession(session: AuthSession | null) {
  currentSession = session;
  emit();
}

function sessionFromLogin(result: LoginResponse): AuthSession {
  return {
    userId: result.user.id,
    displayName: result.user.nickname,
    email: result.user.email,
    tenantId: result.user.tenantId,
    tenantName: result.user.tenantName,
    role: result.user.role,
    platformRoles: result.user.platformRoles ?? [],
    accessToken: result.accessToken,
    expiresIn: result.expiresIn,
  };
}

async function installLogin(result: LoginResponse): Promise<AuthSession> {
  await clearPrivateSessionState();
  setAccessToken(result.accessToken);
  const session = sessionFromLogin(result);
  setSession(session);
  return session;
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 订阅当前登录会话（无 React Query 依赖，供任何组件读取）。 */
export function useAuthSession(): AuthSession | null {
  return useSyncExternalStore(subscribeSession, getSession);
}

/** 登录：换取访问令牌（仅内存），建立会话。 */
export async function login(
  email: string,
  password: string,
): Promise<LoginResponse> {
  const result = await identityApi.login(email, password);
  await installLogin(result);
  return result;
}

/** 退出登录：先撤销服务端 refresh session，再清空全部私有状态。 */
export async function logout(options: { notifyServer?: boolean } = {}) {
  if (options.notifyServer !== false) {
    try {
      await identityApi.logoutSession();
    } catch {
      // 本地退出必须可靠；服务端 refresh token 仍会在到期后失效。
    }
  }
  setAccessToken(null);
  setSession(null);
  await clearPrivateSessionState();
}

/** 浏览器刷新后使用 HttpOnly cookie 恢复短期访问令牌。 */
export async function bootstrapSession(): Promise<AuthSession | null> {
  bootstrapPromise ??= (async () => {
    try {
      return await installLogin(await identityApi.refreshSession());
    } catch {
      await logout({ notifyServer: false });
      return null;
    } finally {
      bootstrapPromise = null;
    }
  })();
  return bootstrapPromise;
}

/** 切换租户必须由后端重新验证 membership 并签发新的 tenant-bound JWT。 */
export async function switchTenant(tenantId: string): Promise<AuthSession> {
  return installLogin(await identityApi.switchTenant(tenantId));
}

/** 资料更新后同步会话昵称（侧栏等即时反映）。 */
export function updateSessionDisplayName(displayName: string) {
  if (currentSession) {
    setSession({ ...currentSession, displayName });
  }
}

export function canViewOperations(session: AuthSession | null): boolean {
  return session?.role === "OWNER" || session?.role === "ADMIN";
}

configureAuthLifecycle({
  refresh: async () => (await bootstrapSession())?.accessToken ?? null,
  onAuthFailure: () => logout({ notifyServer: false }),
});

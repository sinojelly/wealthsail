import { useSyncExternalStore } from "react";
import { setAccessToken } from "../api/client";
import * as identityApi from "../api/identity";
import type { LoginResponse, TenantRole } from "../api/types";

export interface AuthSession {
  userId: string;
  displayName: string;
  email: string | null;
  tenantId: string;
  tenantName: string;
  role: TenantRole;
  accessToken: string;
  expiresIn: number;
}

let currentSession: AuthSession | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function getSession(): AuthSession | null {
  return currentSession;
}

function setSession(session: AuthSession | null) {
  currentSession = session;
  emit();
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
  setAccessToken(result.accessToken);
  setSession({
    userId: result.user.id,
    displayName: result.user.nickname,
    email: result.user.email,
    tenantId: result.user.tenantId,
    tenantName: result.user.tenantName,
    role: result.user.role,
    accessToken: result.accessToken,
    expiresIn: result.expiresIn,
  });
  return result;
}

/** 退出登录：清空内存令牌与会话。 */
export function logout() {
  setAccessToken(null);
  setSession(null);
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

import type { AuthSession } from "./auth-session";
import type { PlatformRole, TenantRole } from "../api/types";

/**
 * 多账号本地记忆：登录过的账号保存在 localStorage，
 * 附近期内（token 未过期）可在账号之间直接切换，无需重新登录。
 * token 过期后该账号标记为"已过期"，重新登录即可刷新。
 */

const STORAGE_KEY = "wealthsail.accounts.v1";

export interface StoredAccount {
  userId: string;
  displayName: string;
  email: string | null;
  tenantId: string;
  tenantName: string;
  role: TenantRole;
  platformRoles: PlatformRole[];
  accessToken: string;
  expiresAt: number;
}

export function listStoredAccounts(): StoredAccount[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as StoredAccount[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveAccounts(accounts: StoredAccount[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(accounts));
}

export function rememberAccount(session: AuthSession) {
  const accounts = listStoredAccounts().filter(
    (account) => account.userId !== session.userId,
  );
  accounts.unshift({
    userId: session.userId,
    displayName: session.displayName,
    email: session.email,
    tenantId: session.tenantId,
    tenantName: session.tenantName,
    role: session.role,
    platformRoles: session.platformRoles,
    accessToken: session.accessToken,
    expiresAt: Date.now() + session.expiresIn * 1000,
  });
  saveAccounts(accounts.slice(0, 8));
}

export function removeStoredAccount(userId: string) {
  saveAccounts(listStoredAccounts().filter((a) => a.userId !== userId));
}

export function currentUserId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY + ".current");
  } catch {
    return null;
  }
}

export function setCurrentUserId(userId: string) {
  try {
    localStorage.setItem(STORAGE_KEY + ".current", userId);
  } catch {
    // 忽略存储异常
  }
}

export function clearCurrentUserId() {
  try {
    localStorage.removeItem(STORAGE_KEY + ".current");
  } catch {
    // 忽略存储异常
  }
}

export function isExpired(account: StoredAccount): boolean {
  return Date.now() >= account.expiresAt;
}

/** 从 AuthSession 生成可存储的账号（切换回来时还原会话）。 */
export function toStoredAccount(session: AuthSession): StoredAccount {
  return {
    userId: session.userId,
    displayName: session.displayName,
    email: session.email,
    tenantId: session.tenantId,
    tenantName: session.tenantName,
    role: session.role,
    platformRoles: session.platformRoles,
    accessToken: session.accessToken,
    expiresAt: Date.now() + session.expiresIn * 1000,
  };
}

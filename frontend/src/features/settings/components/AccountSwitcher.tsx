import { useState } from "react";
import { Check, LogIn, Plus, Trash2, UserRound, X } from "lucide-react";
import {
  isExpired,
  listStoredAccounts,
  removeStoredAccount,
  type StoredAccount,
} from "../../../app/multi-account";
import { installLogin, login, useAuthSession } from "../../../app/auth-session";

/**
 * 多账号切换面板：列出本机记住的账号，点击即切换（token 未过期时），
 * 过期账号可移除后重新添加；支持直接添加新账号（登录表单）。
 */
export default function AccountSwitcher() {
  const session = useAuthSession();
  const [accounts, setAccounts] = useState<StoredAccount[]>(() =>
    listStoredAccounts(),
  );
  const [adding, setAdding] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function refresh() {
    setAccounts(listStoredAccounts());
  }

  async function handleSwitch(account: StoredAccount) {
    if (isExpired(account) || busy) return;
    setBusy(true);
    setError("");
    try {
      await installLogin({
        accessToken: account.accessToken,
        tokenType: "Bearer",
        expiresIn: Math.max(
          60,
          Math.round((account.expiresAt - Date.now()) / 1000),
        ),
        user: {
          id: account.userId,
          nickname: account.displayName,
          email: account.email ?? "",
          tenantId: account.tenantId,
          tenantName: account.tenantName,
          role: account.role,
          platformRoles: account.platformRoles ?? [],
        },
      });
    } catch {
      setError("切换失败，该账号可能需要重新登录");
    } finally {
      setBusy(false);
    }
  }

  function handleRemove(userId: string) {
    removeStoredAccount(userId);
    refresh();
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !email.trim() || !password) return;
    setBusy(true);
    setError("");
    try {
      await login(email.trim(), password);
      refresh();
      setAdding(false);
      setPassword("");
    } catch {
      setError("登录失败，请检查邮箱与密码");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="account-switcher">
      <ul className="account-switcher-list">
        {session ? (
          <li className="account-switcher-item is-current">
            <span className="account-switcher-avatar">
              {session.displayName.slice(0, 1)}
            </span>
            <span className="account-switcher-copy">
              <strong>{session.displayName}</strong>
              <small>{session.email ?? ""}</small>
            </span>
            <em className="account-switcher-badge">
              <Check size={13} />
              当前
            </em>
          </li>
        ) : null}
        {accounts
          .filter((account) => account.userId !== session?.userId)
          .map((account) => {
            const expired = isExpired(account);
            return (
              <li key={account.userId} className="account-switcher-item">
                <button
                  type="button"
                  className="account-switcher-main"
                  disabled={busy || expired}
                  onClick={() => void handleSwitch(account)}
                  title={expired ? "登录已过期，请移除后重新添加" : "切换到此账号"}
                >
                  <span className="account-switcher-avatar">
                    {account.displayName.slice(0, 1)}
                  </span>
                  <span className="account-switcher-copy">
                    <strong>{account.displayName}</strong>
                    <small>{account.email ?? ""}</small>
                  </span>
                  {expired ? (
                    <em className="account-switcher-badge is-expired">已过期</em>
                  ) : (
                    <LogIn size={15} />
                  )}
                </button>
                <button
                  type="button"
                  className="account-switcher-remove"
                  aria-label="移除此账号"
                  onClick={() => handleRemove(account.userId)}
                >
                  <Trash2 size={13} />
                </button>
              </li>
            );
          })}
      </ul>

      {adding ? (
        <form className="account-switcher-form" onSubmit={(e) => void handleAdd(e)}>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="邮箱"
            autoComplete="username"
            required
          />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="密码"
            autoComplete="current-password"
            required
          />
          <div className="account-switcher-form-actions">
            <button type="submit" className="btn-primary btn-sm" disabled={busy}>
              <UserRound size={13} />
              登录并添加
            </button>
            <button
              type="button"
              className="btn-ghost btn-sm"
              onClick={() => {
                setAdding(false);
                setError("");
              }}
            >
              <X size={13} />
              取消
            </button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          className="account-switcher-add"
          onClick={() => setAdding(true)}
        >
          <Plus size={15} />
          添加账号
        </button>
      )}

      {error && <p className="form-error">{error}</p>}
    </div>
  );
}

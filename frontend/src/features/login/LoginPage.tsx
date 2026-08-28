import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  LogIn,
  Mail,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";
import { login } from "../../app/auth-session";
import {
  confirmPasswordReset,
  register,
  registerWithTenant,
  requestPasswordReset,
} from "../../api/identity";

type Mode = "login" | "invited-register" | "self-register" | "password-reset";
const MIN_PASSWORD_LENGTH = 8;

export default function LoginPage() {
  const navigate = useNavigate();
  const initialResetToken = new URLSearchParams(window.location.search).get("token") ?? "";
  const [mode, setMode] = useState<Mode>(initialResetToken ? "password-reset" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [tenantName, setTenantName] = useState("");
  const [invitationToken, setInvitationToken] = useState("");
  const [resetToken, setResetToken] = useState(initialResetToken);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const needsNewPassword =
    mode === "invited-register" || mode === "self-register" || Boolean(resetToken);
  const passwordLength = Array.from(password).length;
  const passwordValid = !needsNewPassword || passwordLength >= MIN_PASSWORD_LENGTH;

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    setNotice(null);
    setPassword("");
    if (next !== "password-reset") setResetToken("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!passwordValid) {
      setError(`密码至少需要 ${MIN_PASSWORD_LENGTH} 个字符`);
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "invited-register") {
        await register({
          email: email.trim(),
          password,
          invitationToken: invitationToken.trim(),
          nickname: nickname.trim() || undefined,
        });
        setNotice("受邀账号已创建，请使用新密码登录");
        setMode("login");
      } else if (mode === "self-register") {
        await registerWithTenant({
          email: email.trim(),
          password,
          nickname: nickname.trim() || undefined,
          name: tenantName.trim(),
          baseCurrency: "CNY",
        });
        setNotice("账号和租户已创建，请登录");
        setMode("login");
      } else if (mode === "password-reset" && resetToken) {
        await confirmPasswordReset(resetToken, password);
        setNotice("密码已重置，请使用新密码登录");
        setMode("login");
        setResetToken("");
      } else if (mode === "password-reset") {
        await requestPasswordReset(email.trim());
        setNotice("如果该邮箱已注册，重置邮件将很快送达");
      } else {
        await login(email.trim(), password);
        navigate("/dashboard", { replace: true });
      }
      setPassword("");
      setInvitationToken("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  const submitLabel =
    mode === "invited-register"
      ? "接受邀请并创建账号"
      : mode === "self-register"
        ? "创建账号和租户"
        : mode === "password-reset"
          ? resetToken
            ? "确认重置密码"
            : "发送重置邮件"
          : "登录工作台";

  return (
    <div className="login-page">
      <div className="login-glow login-glow-a" />
      <div className="login-glow login-glow-b" />
      <div className="login-shell">
        <header className="login-hero">
          <div className="login-logo">
            <span className="login-logo-mark">W</span>
            <span className="login-logo-name">WealthSail</span>
          </div>
          <h1>让每一笔投资，都有据可依</h1>
          <p className="login-slogan">资产看板 · 研报审计 · 风控政策 · 个人投研工作台</p>
        </header>

        <div className="login-card glass">
          <div className="login-tabs">
            <button className={mode === "login" ? "active" : ""} type="button" onClick={() => switchMode("login")}>登录</button>
            <button className={mode === "invited-register" ? "active" : ""} type="button" onClick={() => switchMode("invited-register")}>受邀注册</button>
            <button className={mode === "self-register" ? "active" : ""} type="button" onClick={() => switchMode("self-register")}>自助注册</button>
          </div>

          <form onSubmit={submit} className="login-form" noValidate>
            {mode === "invited-register" ? (
              <label className="login-field">
                <span>邀请令牌</span>
                <div className="login-input">
                  <KeyRound size={15} />
                  <input value={invitationToken} onChange={(event) => setInvitationToken(event.target.value)} required minLength={16} autoComplete="one-time-code" />
                </div>
              </label>
            ) : null}

            {mode === "self-register" ? (
              <label className="login-field">
                <span>租户名称</span>
                <div className="login-input">
                  <UserRound size={15} />
                  <input value={tenantName} onChange={(event) => setTenantName(event.target.value)} placeholder="例如：我的投资组合" required minLength={2} />
                </div>
              </label>
            ) : null}

            {mode === "invited-register" || mode === "self-register" ? (
              <label className="login-field">
                <span>昵称（可选）</span>
                <div className="login-input">
                  <UserRound size={15} />
                  <input value={nickname} onChange={(event) => setNickname(event.target.value)} autoComplete="nickname" />
                </div>
              </label>
            ) : null}

            {!resetToken ? (
              <label className="login-field">
                <span>邮箱</span>
                <div className="login-input">
                  <Mail size={15} />
                  <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@example.com" autoComplete="username" required />
                </div>
              </label>
            ) : null}

            {mode !== "password-reset" || resetToken ? (
              <label className="login-field">
                <span>{resetToken ? "新密码" : "登录密码"}</span>
                <div className="login-input">
                  <LockKeyhole size={15} />
                  <input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete={needsNewPassword ? "new-password" : "current-password"}
                    minLength={needsNewPassword ? MIN_PASSWORD_LENGTH : 1}
                    required
                    aria-describedby={needsNewPassword ? "password-requirement" : undefined}
                  />
                </div>
              </label>
            ) : null}

            {needsNewPassword ? (
              <small id="password-requirement" className={`password-requirement ${passwordValid ? "valid" : "invalid"}`} aria-live="polite">
                {passwordValid ? <ShieldCheck size={13} /> : <ShieldAlert size={13} />}
                <span>{passwordLength === 0 ? "密码要求：至少 8 个字符" : passwordValid ? "已满足：至少 8 个字符" : `还需 ${8 - passwordLength} 个字符（至少 8 个字符）`}</span>
              </small>
            ) : null}

            {error ? <div className="alert alert-danger login-alert" role="alert"><ShieldAlert size={15} />{error}</div> : null}
            {notice ? <div className="alert alert-info login-alert" role="status"><Sparkles size={15} />{notice}</div> : null}

            <button type="submit" className="btn-primary login-submit" disabled={busy || !passwordValid}>
              {busy ? <LoaderCircle size={15} className="spin" /> : mode === "login" ? <LogIn size={15} /> : <UserRound size={15} />}
              {busy ? "处理中…" : submitLabel}
            </button>

            {mode === "login" ? <button type="button" className="btn-ghost" onClick={() => switchMode("password-reset")}>忘记密码？</button> : null}
            {mode === "password-reset" ? <button type="button" className="btn-ghost" onClick={() => switchMode("login")}>返回登录</button> : null}
          </form>
        </div>
      </div>
    </div>
  );
}

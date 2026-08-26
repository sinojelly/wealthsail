import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  LogIn,
  Mail,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";
import { login } from "../../app/auth-session";
import { register } from "../../api/identity";

type Mode = "login" | "invited-register";

export default function LoginPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [invitationToken, setInvitationToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
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
        setNotice("受邀账号已创建，请使用新口令登录");
        setMode("login");
        setPassword("");
        setInvitationToken("");
      } else {
        await login(email.trim(), password);
        navigate("/dashboard", { replace: true });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  const isRegistration = mode === "invited-register";
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
          <p className="login-slogan">
            资产看板 · 研报审计 · 风控政策 · 运维监控 —— 个人投研工作台
          </p>
        </header>

        <div className="login-card glass">
          <div className="login-tabs">
            <button
              className={mode === "login" ? "active" : ""}
              type="button"
              onClick={() => {
                setMode("login");
                setError(null);
              }}
            >
              登录
            </button>
            <button
              className={isRegistration ? "active" : ""}
              type="button"
              onClick={() => {
                setMode("invited-register");
                setError(null);
              }}
            >
              受邀注册
            </button>
          </div>

          <form onSubmit={submit} className="login-form" noValidate>
            {isRegistration ? (
              <>
                <label className="login-field">
                  <span>邀请令牌</span>
                  <div className="login-input">
                    <KeyRound size={15} />
                    <input
                      type="text"
                      autoComplete="one-time-code"
                      value={invitationToken}
                      onChange={(event) => setInvitationToken(event.target.value)}
                      placeholder="粘贴管理员发给你的一次性令牌"
                      required
                      minLength={16}
                    />
                  </div>
                </label>
                <label className="login-field">
                  <span>昵称（可选）</span>
                  <div className="login-input">
                    <UserRound size={15} />
                    <input
                      type="text"
                      autoComplete="nickname"
                      value={nickname}
                      onChange={(event) => setNickname(event.target.value)}
                      placeholder="怎么称呼你"
                    />
                  </div>
                </label>
              </>
            ) : null}
            <label className="login-field">
              <span>邮箱</span>
              <div className="login-input">
                <Mail size={15} />
                <input
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="you@example.com"
                  required
                />
              </div>
            </label>
            <label className="login-field">
              <span>登录口令</span>
              <div className="login-input">
                <LockKeyhole size={15} />
                <input
                  type="password"
                  autoComplete={isRegistration ? "new-password" : "current-password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder={isRegistration ? "至少 8 个字符" : "输入口令"}
                  required
                  minLength={isRegistration ? 8 : 1}
                />
              </div>
            </label>

            {error ? (
              <div className="alert alert-danger login-alert" role="alert">
                <ShieldCheck size={15} />
                {error}
              </div>
            ) : null}
            {notice ? (
              <div className="alert alert-info login-alert" role="status">
                <Sparkles size={15} />
                {notice}
              </div>
            ) : null}

            <button type="submit" className="btn-primary login-submit" disabled={busy}>
              {busy ? (
                <LoaderCircle size={15} className="spin" />
              ) : isRegistration ? (
                <UserRound size={15} />
              ) : (
                <LogIn size={15} />
              )}
              {busy
                ? isRegistration
                  ? "创建中…"
                  : "登录中…"
                : isRegistration
                  ? "接受邀请并创建账号"
                  : "登录工作台"}
            </button>
          </form>

          <div className="login-demo">
            <span className="login-demo-title">
              <ShieldCheck size={12} />
              当前为 2–3 用户封闭试点，不开放公共注册
            </span>
            <p>新成员须由家庭账户所有者或管理员生成一次性邀请令牌。</p>
          </div>
        </div>

        <footer className="login-footer">
          访问令牌仅驻留内存 · 刷新令牌使用 HttpOnly / SameSite Cookie
        </footer>
      </div>
    </div>
  );
}

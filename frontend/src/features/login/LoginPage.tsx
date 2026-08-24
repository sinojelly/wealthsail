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

const DEMO_ACCOUNTS = [
  { label: "张明 · 稳健全天候", email: "zhangming@example.com", tone: "teal" },
  { label: "李华 · 科技高弹性", email: "lihua@example.com", tone: "purple" },
  { label: "王芳 · 红利固收防守", email: "wangfang@example.com", tone: "blue" },
];

type Mode = "login" | "register";

export default function LoginPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      if (mode === "register") {
        await register({ email: email.trim(), password, nickname: nickname.trim() || undefined });
        setNotice("注册成功，请使用新账号登录");
        setMode("login");
        setPassword("");
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

  async function quickLogin(demoEmail: string) {
    setMode("login");
    setEmail(demoEmail);
    setPassword("demo1234");
    setBusy(true);
    setError(null);
    try {
      await login(demoEmail, "demo1234");
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败，请重试");
    } finally {
      setBusy(false);
    }
  }

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
              onClick={() => {
                setMode("login");
                setError(null);
              }}
            >
              登录
            </button>
            <button
              className={mode === "register" ? "active" : ""}
              onClick={() => {
                setMode("register");
                setError(null);
              }}
            >
              注册
            </button>
          </div>

          <form onSubmit={submit} className="login-form" noValidate>
            {mode === "register" ? (
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
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder={mode === "login" ? "输入口令" : "至少 8 个字符"}
                  required
                  minLength={mode === "login" ? 1 : 8}
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

            <button
              type="submit"
              className="btn-primary login-submit"
              disabled={busy}
            >
              {busy ? (
                <LoaderCircle size={15} className="spin" />
              ) : mode === "login" ? (
                <LogIn size={15} />
              ) : (
                <UserRound size={15} />
              )}
              {busy
                ? mode === "login"
                  ? "登录中…"
                  : "注册中…"
                : mode === "login"
                  ? "登录工作台"
                  : "创建账号"}
            </button>
          </form>

          <div className="login-demo">
            <span className="login-demo-title">
              <Sparkles size={12} />
              演示租户（口令 demo1234）
            </span>
            <div className="demo-accounts">
              {DEMO_ACCOUNTS.map((account) => (
                <button
                  key={account.email}
                  className={`demo-account demo-${account.tone}`}
                  disabled={busy}
                  onClick={() => quickLogin(account.email)}
                >
                  <KeyRound size={13} />
                  <span>
                    <strong>{account.label.split(" · ")[0]}</strong>
                    <small>{account.label.split(" · ")[1]}</small>
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>

        <footer className="login-footer">
          访问令牌仅驻留内存 · 口令以 bcrypt 哈希存储，明文不落库
        </footer>
      </div>
    </div>
  );
}

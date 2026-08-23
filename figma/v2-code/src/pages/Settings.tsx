import { useState, useEffect } from "react";
import {
  Copy,
  QrCode,
  CheckCircle2,
  Clock,
  AlertCircle,
  Unlink,
  Shield,
  User,
  RefreshCw,
} from "lucide-react";

export default function Settings({ role }: { role: string }) {
  const [bindingState, setBindingState] = useState<
    "idle" | "generating" | "pending" | "active" | "revoked"
  >("idle");
  const [token, setToken] = useState("");
  const [countdown, setCountdown] = useState(0);
  const [copied, setCopied] = useState(false);
  const [showRevoke, setShowRevoke] = useState(false);

  useEffect(() => {
    if (countdown > 0) {
      const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
      return () => clearTimeout(t);
    }
  }, [countdown]);

  const handleGenerate = () => {
    setBindingState("generating");
    setTimeout(() => {
      setToken("WS-7J4MK9Q2X8NP5R6TC3VDH5YA6B");
      setCountdown(900);
      setBindingState("pending");
    }, 800);
  };

  const handleCopy = () => {
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleRevoke = () => {
    setShowRevoke(false);
    setBindingState("revoked");
  };

  const minutes = Math.floor(countdown / 60);
  const seconds = countdown % 60;

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-semibold" style={{ color: "#0F172A" }}>
          账号、安全与企业微信绑定
        </h1>
        <p className="text-sm mt-1" style={{ color: "#64748B" }}>
          管理当前租户身份、角色权限和企业微信绑定状态。
        </p>
      </div>

      {/* Tenant & role card */}
      <div
        className="rounded-xl border overflow-hidden"
        style={{ borderColor: "#E2E8F0" }}
      >
        <div
          className="px-5 py-3.5 border-b flex items-center gap-2"
          style={{ borderColor: "#F1F5F9", background: "#F8FAFC" }}
        >
          <User size={14} style={{ color: "#0D9488" }} />
          <span className="text-sm font-semibold" style={{ color: "#0F172A" }}>
            租户与角色
          </span>
        </div>
        <div className="px-5 py-4 space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span style={{ color: "#64748B" }}>活动租户</span>
            <div className="flex items-center gap-2">
              <span className="font-medium" style={{ color: "#0F172A" }}>
                李明远的投研空间
              </span>
              <span
                className="font-mono text-xs px-2 py-0.5 rounded"
                style={{ background: "#F1F5F9", color: "#94A3B8" }}
              >
                t_••••••4a2f
              </span>
            </div>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span style={{ color: "#64748B" }}>当前用户</span>
            <span className="font-medium" style={{ color: "#0F172A" }}>
              李明远
            </span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span style={{ color: "#64748B" }}>角色权限</span>
            <span
              className="text-xs font-mono font-semibold px-2.5 py-1 rounded-full"
              style={{ background: "#0D9488", color: "#fff" }}
            >
              {role}
            </span>
          </div>
          <div className="flex items-center justify-between text-sm">
            <span style={{ color: "#64748B" }}>会话过期</span>
            <span className="font-mono text-sm" style={{ color: "#475569" }}>
              2026-08-23 23:59:59
            </span>
          </div>
        </div>
      </div>

      {/* WeCom binding card */}
      <div
        className="rounded-xl border overflow-hidden"
        style={{ borderColor: "#E2E8F0" }}
      >
        <div
          className="px-5 py-3.5 border-b flex items-center gap-2"
          style={{ borderColor: "#F1F5F9", background: "#F8FAFC" }}
        >
          <Shield size={14} style={{ color: "#0D9488" }} />
          <span className="text-sm font-semibold" style={{ color: "#0F172A" }}>
            企业微信绑定
          </span>
          <span className="ml-auto">
            {bindingState === "active" && (
              <span
                className="text-xs px-2 py-0.5 rounded-full font-medium"
                style={{ background: "#DCFCE7", color: "#166534" }}
              >
                ● ACTIVE
              </span>
            )}
            {bindingState === "pending" && (
              <span
                className="text-xs px-2 py-0.5 rounded-full font-medium"
                style={{ background: "#FFFBEB", color: "#92400E" }}
              >
                ● PENDING
              </span>
            )}
            {bindingState === "revoked" && (
              <span
                className="text-xs px-2 py-0.5 rounded-full font-medium"
                style={{ background: "#FEE2E2", color: "#991B1B" }}
              >
                ● REVOKED
              </span>
            )}
            {bindingState === "idle" && (
              <span
                className="text-xs px-2 py-0.5 rounded-full font-medium"
                style={{ background: "#F1F5F9", color: "#64748B" }}
              >
                未绑定
              </span>
            )}
          </span>
        </div>

        <div className="px-5 py-5">
          {/* Active state */}
          {bindingState === "active" && (
            <div className="space-y-4">
              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span style={{ color: "#64748B" }}>绑定账号</span>
                  <span className="font-mono" style={{ color: "#0F172A" }}>
                    企业微信 ••••2847
                  </span>
                </div>
                <div className="flex justify-between">
                  <span style={{ color: "#64748B" }}>绑定时间</span>
                  <span className="font-mono" style={{ color: "#0F172A" }}>
                    2026-07-15 09:42:11
                  </span>
                </div>
                <div className="flex justify-between">
                  <span style={{ color: "#64748B" }}>状态</span>
                  <CheckCircle2 size={16} style={{ color: "#10B981" }} />
                </div>
              </div>
              <div className="flex items-center gap-2 pt-2">
                <button
                  onClick={() => setShowRevoke(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded border text-xs font-medium transition-colors hover:bg-red-50"
                  style={{ borderColor: "#FCA5A5", color: "#DC2626" }}
                >
                  <Unlink size={12} />
                  解绑企业微信
                </button>
                <button
                  onClick={handleGenerate}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded border text-xs font-medium transition-colors hover:bg-gray-50"
                  style={{ borderColor: "#E2E8F0", color: "#475569" }}
                >
                  <RefreshCw size={12} />
                  重新绑定
                </button>
              </div>
            </div>
          )}

          {/* Idle state */}
          {bindingState === "idle" && (
            <div className="space-y-4">
              <p className="text-sm" style={{ color: "#64748B" }}>
                绑定后可通过企业微信接收 08:30 盘前剧本、盘中触点告警和 15:30 复盘推送。
              </p>
              <button
                onClick={handleGenerate}
                className="flex items-center gap-2 px-4 py-2 rounded text-sm font-medium"
                style={{ background: "#0D9488", color: "#fff" }}
              >
                生成绑定码
              </button>
            </div>
          )}

          {/* Generating */}
          {bindingState === "generating" && (
            <div className="flex items-center gap-3 py-4">
              <RefreshCw size={16} className="animate-spin" style={{ color: "#0D9488" }} />
              <span className="text-sm" style={{ color: "#64748B" }}>
                正在生成一次性绑定码...
              </span>
            </div>
          )}

          {/* Pending state */}
          {bindingState === "pending" && (
            <div className="space-y-4">
              <div
                className="flex items-center gap-2 text-sm"
                style={{ color: "#92400E" }}
              >
                <Clock size={14} style={{ color: "#F59E0B" }} />
                <span>
                  绑定码有效期剩余{" "}
                  <strong className="font-mono">
                    {minutes}:{String(seconds).padStart(2, "0")}
                  </strong>
                  ，请尽快操作
                </span>
              </div>

              {/* Token display */}
              <div
                className="rounded-lg border p-4"
                style={{ borderColor: "#E2E8F0", background: "#F8FAFC" }}
              >
                <div className="text-xs font-medium mb-2" style={{ color: "#64748B" }}>
                  一次性绑定码（128-bit 随机熵）
                </div>
                <div className="flex items-center gap-3">
                  <code
                    className="flex-1 font-mono text-base font-semibold tracking-wider"
                    style={{ color: "#0D9488" }}
                  >
                    {token}
                  </code>
                  <button
                    onClick={handleCopy}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded border text-xs transition-colors"
                    style={{
                      borderColor: copied ? "#86EFAC" : "#E2E8F0",
                      background: copied ? "#F0FDF4" : "#fff",
                      color: copied ? "#166534" : "#475569",
                    }}
                  >
                    {copied ? <CheckCircle2 size={12} /> : <Copy size={12} />}
                    {copied ? "已复制" : "复制"}
                  </button>
                </div>
              </div>

              {/* QR code placeholder */}
              <div
                className="flex items-center gap-4 rounded-lg border p-4"
                style={{ borderColor: "#E2E8F0" }}
              >
                <div
                  className="shrink-0 rounded-lg flex items-center justify-center"
                  style={{ width: 80, height: 80, background: "#F1F5F9" }}
                >
                  <QrCode size={32} style={{ color: "#CBD5E1" }} />
                </div>
                <div className="text-sm space-y-1.5">
                  <p className="font-medium" style={{ color: "#0F172A" }}>
                    企业微信扫码或发送命令
                  </p>
                  <p style={{ color: "#64748B" }}>
                    在企业微信 WealthSail 私聊发送：
                  </p>
                  <code
                    className="block font-mono text-sm px-3 py-1.5 rounded"
                    style={{ background: "#0F172A", color: "#0D9488" }}
                  >
                    /bind {token}
                  </code>
                </div>
              </div>

              <div
                className="flex items-start gap-2 text-xs p-3 rounded-lg"
                style={{ background: "#F8FAFC", color: "#64748B" }}
              >
                <AlertCircle size={12} className="shrink-0 mt-0.5" />
                <span>
                  绑定码仅显示一次，请勿截图或分享给他人。响应标记
                  <code className="font-mono mx-1 px-1 rounded" style={{ background: "#E2E8F0" }}>
                    Cache-Control: no-store
                  </code>
                  ，不会留在浏览器缓存。
                </span>
              </div>
            </div>
          )}

          {/* Revoked */}
          {bindingState === "revoked" && (
            <div className="space-y-4">
              <div
                className="flex items-center gap-2 text-sm p-3 rounded-lg"
                style={{ background: "#FEF2F2", color: "#991B1B" }}
              >
                <AlertCircle size={14} />
                绑定已解除（REVOKED）。企业微信将不再收到推送，直至重新绑定。
              </div>
              <button
                onClick={handleGenerate}
                className="flex items-center gap-2 px-4 py-2 rounded text-sm font-medium"
                style={{ background: "#0D9488", color: "#fff" }}
              >
                重新生成绑定码
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Security notes */}
      <div
        className="rounded-xl border p-4 space-y-2"
        style={{ borderColor: "#E2E8F0", background: "#F8FAFC" }}
      >
        <p className="text-xs font-semibold uppercase tracking-wider" style={{ color: "#64748B" }}>
          安全说明
        </p>
        <ul className="text-xs space-y-1.5" style={{ color: "#94A3B8" }}>
          <li>· access token 仅保存于内存，不写入 localStorage 或 URL</li>
          <li>· refresh token 存放于 Secure/HttpOnly/SameSite Cookie，刷新与登出校验 CSRF/Origin</li>
          <li>· 绑定码具有至少 128-bit 随机熵，仅在本次响应出现，后续接口不返回</li>
          <li>· 跨用户解绑操作需 OWNER/ADMIN 重新认证并产生独立审计记录</li>
        </ul>
      </div>

      {/* Revoke confirm dialog */}
      {showRevoke && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0"
            style={{ background: "rgba(15,23,42,0.4)" }}
            onClick={() => setShowRevoke(false)}
          />
          <div
            className="relative bg-white rounded-xl shadow-2xl w-full max-w-sm mx-4 p-6"
            style={{ border: "1px solid #E2E8F0" }}
          >
            <h3 className="font-semibold mb-2" style={{ color: "#0F172A" }}>
              确认解除绑定？
            </h3>
            <p className="text-sm mb-4" style={{ color: "#64748B" }}>
              解绑后企业微信将停止接收所有推送。操作将标记为
              <code className="font-mono mx-1 text-xs px-1.5 py-0.5 rounded" style={{ background: "#F1F5F9" }}>
                REVOKED
              </code>
              并记录审计日志，不执行物理删除。
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowRevoke(false)}
                className="px-4 py-2 rounded border text-sm"
                style={{ borderColor: "#E2E8F0", color: "#475569" }}
              >
                取消
              </button>
              <button
                onClick={handleRevoke}
                className="px-4 py-2 rounded text-sm font-semibold"
                style={{ background: "#DC2626", color: "#fff" }}
              >
                确认解绑
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

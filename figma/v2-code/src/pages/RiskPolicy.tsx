import { useState } from "react";
import {
  ShieldCheck,
  AlertTriangle,
  CheckCircle2,
  Info,
  Lock,
  ChevronDown,
  X,
} from "lucide-react";

type Status = "CONFIRMED" | "DRAFT" | "PREVIEWING";

function FieldRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-4 py-4" style={{ borderBottom: "1px solid #F1F5F9" }}>
      <div className="w-56 shrink-0">
        <div className="text-sm font-medium" style={{ color: "#0F172A" }}>
          {label}
        </div>
        {hint && (
          <div className="text-xs mt-0.5" style={{ color: "#94A3B8" }}>
            {hint}
          </div>
        )}
      </div>
      <div className="flex-1">{children}</div>
    </div>
  );
}

function Input({
  value,
  onChange,
  suffix,
  placeholder,
  disabled,
}: {
  value: string;
  onChange?: (v: string) => void;
  suffix?: string;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <div className="relative flex items-center">
      <input
        type="text"
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="w-full text-sm font-mono rounded border px-3 py-2 outline-none transition-colors"
        style={{
          borderColor: "#E2E8F0",
          background: disabled ? "#F8FAFC" : "#fff",
          color: disabled ? "#94A3B8" : "#0F172A",
        }}
        onFocus={(e) => {
          if (!disabled) e.currentTarget.style.borderColor = "#0D9488";
        }}
        onBlur={(e) => {
          e.currentTarget.style.borderColor = "#E2E8F0";
        }}
      />
      {suffix && (
        <span
          className="absolute right-3 text-sm font-mono"
          style={{ color: "#94A3B8" }}
        >
          {suffix}
        </span>
      )}
    </div>
  );
}

export default function RiskPolicy() {
  const [status, setStatus] = useState<Status>("CONFIRMED");
  const [isDraft, setIsDraft] = useState(false);
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  const [benchmark, setBenchmark] = useState("000300.SH");
  const [horizon, setHorizon] = useState("24");
  const [maxPosition, setMaxPosition] = useState("30");
  const [maxDrawdown, setMaxDrawdown] = useState("15");
  const [minCash, setMinCash] = useState("5");
  const [maxAlerts, setMaxAlerts] = useState("5");
  const [silenceWindow, setSilenceWindow] = useState("60");

  const handleEdit = () => {
    setIsDraft(true);
    setStatus("DRAFT");
    setConfirmed(false);
  };

  const handlePreview = () => {
    setStatus("PREVIEWING");
  };

  const handleConfirm = () => {
    setShowConfirmDialog(false);
    setStatus("CONFIRMED");
    setIsDraft(false);
    setConfirmed(true);
  };

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold" style={{ color: "#0F172A" }}>
            L1 投资政策与风控设置
          </h1>
          <p className="text-sm mt-1" style={{ color: "#64748B" }}>
            设置组合基准、仓位上限与风险预算。DRAFT 不参与报告和建议计算。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill status={status} />
        </div>
      </div>

      {/* Confirmed info */}
      {confirmed && (
        <div
          className="flex items-center gap-3 px-4 py-3 rounded-lg border"
          style={{ background: "#F0FDF4", borderColor: "#86EFAC", color: "#166534" }}
        >
          <CheckCircle2 size={15} />
          <span className="text-sm">
            政策已确认生效 · 确认人：李明远（OWNER）· 2026-08-23 16:05:43
          </span>
        </div>
      )}

      {/* DRAFT banner */}
      {isDraft && (
        <div
          className="flex items-center gap-3 px-4 py-3 rounded-lg border"
          style={{ background: "#FFFBEB", borderColor: "#FCD34D", color: "#92400E" }}
        >
          <AlertTriangle size={15} />
          <span className="text-sm">
            当前为 <strong>DRAFT</strong> 草稿状态，修改内容不参与报告、建议或风险计算，需 OWNER/ADMIN 确认生效。
          </span>
        </div>
      )}

      {/* Current confirmed version */}
      <div
        className="rounded-xl border"
        style={{ borderColor: "#E2E8F0", background: "#fff" }}
      >
        <div
          className="flex items-center justify-between px-6 py-4 border-b"
          style={{ borderColor: "#F1F5F9" }}
        >
          <div className="flex items-center gap-2">
            <ShieldCheck size={16} style={{ color: "#0D9488" }} />
            <span className="text-sm font-semibold" style={{ color: "#0F172A" }}>
              当前有效政策
            </span>
            {!isDraft && (
              <span className="text-xs font-mono px-2 py-0.5 rounded" style={{ background: "#F0FDF4", color: "#166534" }}>
                v3 · CONFIRMED
              </span>
            )}
            {isDraft && (
              <span className="text-xs font-mono px-2 py-0.5 rounded" style={{ background: "#FFFBEB", color: "#92400E" }}>
                v3 DRAFT
              </span>
            )}
          </div>
          <div className="flex gap-2">
            {!isDraft ? (
              <button
                onClick={handleEdit}
                className="text-xs px-3 py-1.5 rounded border font-medium transition-colors hover:bg-gray-50"
                style={{ borderColor: "#E2E8F0", color: "#475569" }}
              >
                编辑草稿
              </button>
            ) : status === "DRAFT" ? (
              <>
                <button
                  onClick={handlePreview}
                  className="text-xs px-3 py-1.5 rounded border font-medium transition-colors hover:bg-gray-50"
                  style={{ borderColor: "#E2E8F0", color: "#475569" }}
                >
                  预览影响
                </button>
                <button
                  onClick={() => setShowConfirmDialog(true)}
                  className="text-xs px-3 py-1.5 rounded font-medium transition-colors"
                  style={{ background: "#0D9488", color: "#fff" }}
                >
                  <Lock size={11} className="inline mr-1" />
                  确认生效
                </button>
              </>
            ) : (
              <button
                onClick={() => setShowConfirmDialog(true)}
                className="text-xs px-3 py-1.5 rounded font-medium transition-colors"
                style={{ background: "#0D9488", color: "#fff" }}
              >
                <Lock size={11} className="inline mr-1" />
                确认生效
              </button>
            )}
          </div>
        </div>

        <div className="px-6">
          {/* 改进 #8：影响预览面板，显示具体持仓是否触发超限告警 */}
          {status === "PREVIEWING" && (
            <div className="my-4 space-y-2">
              <div
                className="rounded-lg border p-4 space-y-2"
                style={{ borderColor: "#BFDBFE", background: "#EFF6FF" }}
              >
                <div className="flex items-center gap-2 mb-2">
                  <Info size={14} style={{ color: "#1D4ED8" }} />
                  <span className="text-sm font-semibold" style={{ color: "#1E40AF" }}>
                    影响预览（服务端确定性计算）
                  </span>
                </div>
                <div className="text-xs space-y-1" style={{ color: "#1E40AF" }}>
                  {Number(maxPosition) >= 17.8 ? (
                    <p className="flex items-center gap-1.5">
                      <CheckCircle2 size={11} style={{ color: "#10B981" }} />
                      单仓位上限 {maxPosition}% → 当前无持仓超限（最高 17.8% 贵州茅台）
                    </p>
                  ) : (
                    <p className="flex items-center gap-1.5 font-medium" style={{ color: "#DC2626" }}>
                      <AlertTriangle size={11} />
                      单仓位上限调整为 {maxPosition}%，当前贵州茅台持仓占比 17.8% 将超过新上限，生效后盘中将触发调仓预警
                    </p>
                  )}
                  <p className="flex items-center gap-1.5">
                    <CheckCircle2 size={11} style={{ color: "#10B981" }} />
                    最大回撤容忍 {maxDrawdown}% → 近 3 个月最大回撤 4.2%，未触发警戒
                  </p>
                  <p className="flex items-center gap-1.5">
                    <CheckCircle2 size={11} style={{ color: "#10B981" }} />
                    现金储备下限 {minCash}% → 当前现金占比 10.5%，满足要求
                  </p>
                  <p className="flex items-center gap-1.5" style={{ color: "#92400E" }}>
                    <AlertTriangle size={11} />
                    新政策生效后旧版本（v2）立即退役，不可恢复
                  </p>
                </div>
              </div>
              {Number(maxPosition) < 17.8 && (
                <div
                  className="flex items-start gap-2 rounded-lg border p-3 text-xs"
                  style={{ background: "#FEF2F2", borderColor: "#FCA5A5", color: "#991B1B" }}
                >
                  <AlertTriangle size={13} className="shrink-0 mt-0.5" />
                  <span>
                    <strong>注意：</strong>确认生效后，盘中系统将立即对贵州茅台（600519，当前占比 17.8%）发出调仓预警。
                    如不希望触发预警，建议将单标的上限设为不低于 18%，或先完成调仓再生效新政策。
                  </span>
                </div>
              )}
            </div>
          )}

          <FieldRow label="组合基准" hint="用于超额收益和报告对比计算">
            <div className="relative">
              <select
                value={benchmark}
                onChange={(e) => setBenchmark(e.target.value)}
                disabled={!isDraft}
                className="w-full text-sm font-mono rounded border px-3 py-2 appearance-none outline-none pr-8"
                style={{
                  borderColor: "#E2E8F0",
                  background: !isDraft ? "#F8FAFC" : "#fff",
                  color: !isDraft ? "#94A3B8" : "#0F172A",
                }}
              >
                <option value="000300.SH">沪深 300（000300.SH）</option>
                <option value="000905.SH">中证 500（000905.SH）</option>
                <option value="000016.SH">上证 50（000016.SH）</option>
                <option value="399006.SZ">创业板指（399006.SZ）</option>
              </select>
              <ChevronDown
                size={13}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none"
                style={{ color: "#94A3B8" }}
              />
            </div>
          </FieldRow>

          <FieldRow label="投资期限" hint="映射 investment_horizon_months">
            <Input
              value={horizon}
              onChange={isDraft ? setHorizon : undefined}
              suffix="个月"
              placeholder="请输入投资期限"
              disabled={!isDraft}
            />
          </FieldRow>

          <FieldRow label="单标的最大仓位上限" hint="单只股票/基金占总资产比例上限">
            <Input
              value={maxPosition}
              onChange={isDraft ? setMaxPosition : undefined}
              suffix="%"
              placeholder="例：30（仅示例，请填写实际值）"
              disabled={!isDraft}
            />
            {isDraft && (
              <p className="text-xs mt-1.5" style={{ color: "#94A3B8" }}>
                示例格式：30 表示单标的最大持仓 30%，请根据实际风险偏好填写
              </p>
            )}
          </FieldRow>

          <FieldRow label="组合最大回撤容忍线" hint="触发警戒的回撤百分比">
            <Input
              value={maxDrawdown}
              onChange={isDraft ? setMaxDrawdown : undefined}
              suffix="%"
              placeholder="例：15（仅示例，请填写实际值）"
              disabled={!isDraft}
            />
            {isDraft && (
              <p className="text-xs mt-1.5" style={{ color: "#94A3B8" }}>
                示例格式：15 表示最大回撤警戒线 15%，超过后触发告警提醒
              </p>
            )}
          </FieldRow>

          <FieldRow label="现金储备约束" hint="最低可用现金比例要求">
            <Input
              value={minCash}
              onChange={isDraft ? setMinCash : undefined}
              suffix="% 最低"
              placeholder="例：5（仅示例）"
              disabled={!isDraft}
            />
          </FieldRow>

          <FieldRow label="每日最大提醒条数" hint="防止告警轰炸">
            <Input
              value={maxAlerts}
              onChange={isDraft ? setMaxAlerts : undefined}
              suffix="条/日"
              placeholder=""
              disabled={!isDraft}
            />
          </FieldRow>

          <FieldRow label="同标的静默窗口" hint="服务端默认 60 分钟，以服务端配置为准">
            <Input
              value={silenceWindow}
              onChange={isDraft ? setSilenceWindow : undefined}
              suffix="分钟"
              placeholder=""
              disabled={!isDraft}
            />
          </FieldRow>

          <FieldRow label="允许的标的类型" hint="L1 范围">
            <div className="flex flex-wrap gap-2">
              {["A 股股票", "场内 ETF/LOF", "场外公募基金", "CNY 现金"].map((t) => (
                <span
                  key={t}
                  className="text-xs px-3 py-1 rounded-full"
                  style={{ background: "#F0FDF4", color: "#166534", border: "1px solid #86EFAC" }}
                >
                  {t}
                </span>
              ))}
              {["债券", "黄金/大宗"].map((t) => (
                <span
                  key={t}
                  className="text-xs px-3 py-1 rounded-full"
                  style={{ background: "#F1F5F9", color: "#94A3B8", border: "1px solid #E2E8F0" }}
                  title="L4 预留，不参与 L1 计算"
                >
                  {t} <span className="opacity-50">(L4)</span>
                </span>
              ))}
            </div>
          </FieldRow>
        </div>

        <div
          className="px-6 py-4 border-t text-xs"
          style={{ borderColor: "#F1F5F9", color: "#94A3B8" }}
        >
          版本 v3 · 确认人：李明远 · 2026-08-10 09:22:11 · CONFIRMED
        </div>
      </div>

      {/* Confirm dialog */}
      {showConfirmDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="absolute inset-0"
            style={{ background: "rgba(15,23,42,0.4)" }}
            onClick={() => setShowConfirmDialog(false)}
          />
          <div
            className="relative bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 p-6"
            style={{ border: "1px solid #E2E8F0" }}
          >
            <div className="flex items-center gap-3 mb-4">
              <Lock size={18} style={{ color: "#0D9488" }} />
              <h3 className="font-semibold" style={{ color: "#0F172A" }}>
                确认风险政策生效
              </h3>
            </div>
            <div
              className="rounded-lg p-3 mb-4 text-sm"
              style={{ background: "#FEF9C3", color: "#92400E" }}
            >
              <p className="font-medium mb-1">操作须知</p>
              <ul className="text-xs space-y-1 list-disc list-inside">
                <li>本操作需要 OWNER/ADMIN 权限</li>
                <li>旧版本（v2）将立即退役，不可恢复</li>
                <li>新政策立即参与报告和建议计算</li>
                <li>操作将产生不可撤销的审计记录</li>
              </ul>
            </div>
            <p className="text-sm mb-4" style={{ color: "#475569" }}>
              请确认您已仔细核对所有配置项，并使用 OWNER 身份重新认证。
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowConfirmDialog(false)}
                className="px-4 py-2 rounded border text-sm transition-colors hover:bg-gray-50"
                style={{ borderColor: "#E2E8F0", color: "#475569" }}
              >
                取消
              </button>
              <button
                onClick={handleConfirm}
                className="px-4 py-2 rounded text-sm font-semibold transition-colors"
                style={{ background: "#0D9488", color: "#fff" }}
              >
                重新认证并确认生效
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatusPill({ status }: { status: Status }) {
  const map = {
    CONFIRMED: { bg: "#F0FDF4", color: "#166534", label: "CONFIRMED" },
    DRAFT: { bg: "#FFFBEB", color: "#92400E", label: "DRAFT" },
    PREVIEWING: { bg: "#EFF6FF", color: "#1E40AF", label: "PREVIEWING" },
  };
  const s = map[status];
  return (
    <span
      className="text-xs font-mono px-3 py-1 rounded-full font-semibold"
      style={{ background: s.bg, color: s.color }}
    >
      {s.label}
    </span>
  );
}

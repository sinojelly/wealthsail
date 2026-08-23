import { useState } from "react";
import { CheckCircle2, XCircle, MinusCircle, Clock, RefreshCw } from "lucide-react";

const jobRuns = [
  {
    id: "JR-20260823-001",
    name: "market_data_sync",
    status: "COMPLETED",
    duration: "1.24s",
    startedAt: "2026-08-23 15:35:02",
    correlationId: "corr-7f4a-9e12",
  },
  {
    id: "JR-20260823-002",
    name: "fund_nav_estimate",
    status: "COMPLETED",
    duration: "3.87s",
    startedAt: "2026-08-23 14:30:01",
    correlationId: "corr-3b8c-1d54",
  },
  {
    id: "JR-20260823-003",
    name: "post_market_report",
    status: "COMPLETED",
    duration: "12.41s",
    startedAt: "2026-08-23 15:33:18",
    correlationId: "corr-9a2e-5f71",
  },
  {
    id: "JR-20260823-004",
    name: "benchmark_refresh",
    status: "ABSTAINED",
    duration: "0.12s",
    startedAt: "2026-08-23 09:01:00",
    correlationId: "corr-2c5d-8e33",
    errorClass: "NON_TRADING_DAY",
  },
  {
    id: "JR-20260822-005",
    name: "market_data_sync",
    status: "FAILED",
    duration: "15.00s",
    startedAt: "2026-08-22 15:35:02",
    correlationId: "corr-0f1b-7a96",
    errorClass: "DATA_PROVIDER_TIMEOUT",
  },
];

const auditEvents = [
  {
    id: "AUD-001",
    risk: "HIGH",
    action: "POLICY_CONFIRMED",
    resource: "risk_policy:v3",
    actor: "李明远 (OWNER)",
    at: "2026-08-10 09:22:11",
    correlationId: "corr-4d2a-1f88",
  },
  {
    id: "AUD-002",
    risk: "HIGH",
    action: "IMPORT_CONFIRMED",
    resource: "portfolio_import_batch:BATCH-0001",
    actor: "李明远 (OWNER)",
    at: "2026-07-01 10:15:32",
    correlationId: "corr-8e3c-5b44",
  },
  {
    id: "AUD-003",
    risk: "HIGH",
    action: "BINDING_CREATED",
    resource: "identity_binding:BND-0001",
    actor: "李明远 (OWNER)",
    at: "2026-07-15 09:42:11",
    correlationId: "corr-1a7f-2d90",
  },
];

function StatusIcon({ status }: { status: string }) {
  if (status === "COMPLETED") return <CheckCircle2 size={14} style={{ color: "#10B981" }} />;
  if (status === "FAILED") return <XCircle size={14} style={{ color: "#EF4444" }} />;
  if (status === "ABSTAINED") return <MinusCircle size={14} style={{ color: "#94A3B8" }} />;
  return <Clock size={14} style={{ color: "#F59E0B" }} />;
}

export default function Operations() {
  const [tab, setTab] = useState<"jobs" | "audit">("jobs");

  return (
    <div className="p-6 space-y-6 max-w-screen-xl mx-auto">
      <div>
        <h1 className="text-xl font-semibold" style={{ color: "#0F172A" }}>
          运维监控
        </h1>
        <p className="text-sm mt-1" style={{ color: "#64748B" }}>
          仅 OWNER/ADMIN 可见。展示脱敏任务运行状态与高风险操作审计摘要。
        </p>
      </div>

      {/* Tabs */}
      <div className="flex border-b" style={{ borderColor: "#E2E8F0" }}>
        {(
          [
            { id: "jobs", label: "任务运行记录" },
            { id: "audit", label: "高风险操作审计" },
          ] as const
        ).map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className="px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px"
            style={{
              borderColor: tab === t.id ? "#0D9488" : "transparent",
              color: tab === t.id ? "#0D9488" : "#64748B",
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "jobs" && (
        <div
          className="rounded-xl border overflow-hidden"
          style={{ borderColor: "#E2E8F0" }}
        >
          <div className="flex items-center justify-between px-5 py-3.5 border-b" style={{ borderColor: "#F1F5F9", background: "#F8FAFC" }}>
            <span className="text-sm font-semibold" style={{ color: "#0F172A" }}>
              近 7 日任务运行
            </span>
            <button className="flex items-center gap-1.5 text-xs" style={{ color: "#64748B" }}>
              <RefreshCw size={12} />
              刷新
            </button>
          </div>
          <table className="w-full text-sm">
            <thead style={{ background: "#F8FAFC" }}>
              <tr>
                {["任务名称", "状态", "耗时", "开始时间", "错误类型", "Correlation ID"].map((h) => (
                  <th
                    key={h}
                    className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider whitespace-nowrap"
                    style={{ color: "#64748B", borderBottom: "1px solid #F1F5F9" }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {jobRuns.map((job) => (
                <tr
                  key={job.id}
                  className="hover:bg-gray-50 transition-colors"
                  style={{ borderBottom: "1px solid #F8FAFC" }}
                >
                  <td className="px-4 py-3 font-mono text-xs font-medium" style={{ color: "#0D9488" }}>
                    {job.name}
                  </td>
                  <td className="px-4 py-3">
                    <span className="flex items-center gap-1.5">
                      <StatusIcon status={job.status} />
                      <span
                        className="text-xs font-mono font-medium"
                        style={{
                          color:
                            job.status === "COMPLETED"
                              ? "#10B981"
                              : job.status === "FAILED"
                              ? "#EF4444"
                              : "#94A3B8",
                        }}
                      >
                        {job.status}
                      </span>
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs" style={{ color: "#475569" }}>
                    {job.duration}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs" style={{ color: "#475569" }}>
                    {job.startedAt}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs" style={{ color: "#EF4444" }}>
                    {job.errorClass || "—"}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs" style={{ color: "#94A3B8" }}>
                    {job.correlationId}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "audit" && (
        <div
          className="rounded-xl border overflow-hidden"
          style={{ borderColor: "#E2E8F0" }}
        >
          <div className="px-5 py-3.5 border-b" style={{ borderColor: "#F1F5F9", background: "#F8FAFC" }}>
            <span className="text-sm font-semibold" style={{ color: "#0F172A" }}>
              高风险操作审计摘要（脱敏）
            </span>
          </div>
          <div
            className="px-5 py-3 text-xs border-b"
            style={{ background: "#FFFBEB", borderColor: "#FCD34D", color: "#92400E" }}
          >
            仅展示操作类型和脱敏资源标识。完整 payload、token/hash 和持仓不经浏览器 JWT 暴露。
          </div>
          <table className="w-full text-sm">
            <thead style={{ background: "#F8FAFC" }}>
              <tr>
                {["风险级别", "操作类型", "资源标识", "操作人", "时间", "Correlation ID"].map((h) => (
                  <th
                    key={h}
                    className="text-left px-4 py-3 text-xs font-medium uppercase tracking-wider whitespace-nowrap"
                    style={{ color: "#64748B", borderBottom: "1px solid #F1F5F9" }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {auditEvents.map((ev) => (
                <tr
                  key={ev.id}
                  className="hover:bg-gray-50 transition-colors"
                  style={{ borderBottom: "1px solid #F8FAFC" }}
                >
                  <td className="px-4 py-3">
                    <span
                      className="text-xs font-mono font-semibold px-2 py-0.5 rounded"
                      style={{ background: "#FEE2E2", color: "#991B1B" }}
                    >
                      {ev.risk}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs font-medium" style={{ color: "#0D9488" }}>
                    {ev.action}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs" style={{ color: "#475569" }}>
                    {ev.resource}
                  </td>
                  <td className="px-4 py-3 text-xs" style={{ color: "#0F172A" }}>
                    {ev.actor}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs" style={{ color: "#475569" }}>
                    {ev.at}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs" style={{ color: "#94A3B8" }}>
                    {ev.correlationId}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

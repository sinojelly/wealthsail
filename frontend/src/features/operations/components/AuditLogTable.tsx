import { ExternalLink, Fingerprint, LockKeyhole } from "lucide-react";

const auditRows = [
  {
    id: "aud-0823-001",
    time: "23:07:42",
    action: "policy.confirm",
    actor: "李明远",
    target: "risk_policy/v4",
    risk: "HIGH",
    result: "SUCCESS",
    requestId: "req_7e91a2",
    ip: "192.168.1.18",
  },
  {
    id: "aud-0823-002",
    time: "16:34:11",
    action: "report.publish",
    actor: "system.scheduler",
    target: "R-POST-20260823",
    risk: "MEDIUM",
    result: "SUCCESS",
    requestId: "req_f4391b",
    ip: "service",
  },
  {
    id: "aud-0823-003",
    time: "14:17:26",
    action: "fill.draft.create",
    actor: "李明远",
    target: "F2026082302",
    risk: "MEDIUM",
    result: "SUCCESS",
    requestId: "req_028c72",
    ip: "192.168.1.18",
  },
  {
    id: "aud-0823-004",
    time: "09:32:08",
    action: "wecom.event.receive",
    actor: "wecom:zhangsan.demo",
    target: "F2026082301",
    risk: "LOW",
    result: "SUCCESS",
    requestId: "req_55fb19",
    ip: "wecom-gateway",
  },
];

export default function AuditLogTable() {
  return (
    <div className="table-wrap">
      <table className="data-table audit-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>动作</th>
            <th>执行主体</th>
            <th>目标</th>
            <th>风险级别</th>
            <th>结果</th>
            <th>Request ID / 来源</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          {auditRows.map((row) => (
            <tr key={row.id}>
              <td className="mono">{row.time}</td>
              <td>
                <strong className="mono">{row.action}</strong>
                <small className="mono">{row.id}</small>
              </td>
              <td>
                <Fingerprint size={11} />
                {row.actor}
              </td>
              <td className="mono">{row.target}</td>
              <td>
                <span className={`risk-level ${row.risk.toLowerCase()}`}>
                  {row.risk === "HIGH" ? <LockKeyhole size={10} /> : null}
                  {row.risk}
                </span>
              </td>
              <td>
                <span className="run-result success">{row.result}</span>
              </td>
              <td>
                <strong className="mono">{row.requestId}</strong>
                <small className="mono">{row.ip}</small>
              </td>
              <td>
                <button
                  className="icon-button"
                  aria-label={`查看审计 ${row.id}`}
                >
                  <ExternalLink size={13} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

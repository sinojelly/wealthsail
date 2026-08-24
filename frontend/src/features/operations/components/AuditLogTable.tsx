import { ExternalLink, Fingerprint } from "lucide-react";
import type { AuditRow } from "../../../api/types";

export default function AuditLogTable({ rows }: { rows: AuditRow[] }) {
  return (
    <div className="table-wrap">
      <table className="data-table audit-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>动作</th>
            <th>执行主体</th>
            <th>目标</th>
            <th>Request ID</th>
            <th>详情</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td className="mono">
                {row.createdAt.replace("T", " ").slice(0, 19)}
              </td>
              <td>
                <strong className="mono">{row.action}</strong>
                <small className="mono">{row.id.slice(0, 8)}</small>
              </td>
              <td>
                <Fingerprint size={11} />
                {row.actorType === "USER" ? row.actorId.slice(0, 8) : row.actorId}
              </td>
              <td className="mono">
                {row.resourceType}/{row.resourceId.slice(0, 8)}
              </td>
              <td>
                <strong className="mono">{row.correlationId ?? "—"}</strong>
              </td>
              <td>
                <button
                  className="icon-button"
                  aria-label={`查看审计 ${row.id.slice(0, 8)}`}
                >
                  <ExternalLink size={13} />
                </button>
              </td>
            </tr>
          ))}
          {rows.length === 0 ? (
            <tr>
              <td colSpan={6} className="muted">
                暂无审计事件
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

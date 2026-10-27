import { CheckCircle2, Clock3, RotateCcw, TriangleAlert } from "lucide-react";
import type { OpsJobRun } from "../../../api/types";
import { formatShanghaiTime } from "../format-time";

const STATUS_META: Record<string, { label: string; tone: string }> = {
  COMPLETED: { label: "SUCCESS", tone: "success" },
  FAILED: { label: "FAILED", tone: "failed" },
  ABSTAINED: { label: "ABSTAINED", tone: "warning" },
  RUNNING: { label: "RUNNING", tone: "warning" },
  PENDING: { label: "PENDING", tone: "warning" },
  SKIPPED: { label: "未触发", tone: "neutral" },
};

export default function JobRunsTable({
  jobs,
  runs,
}: {
  jobs: {
    id: string;
    jobName: string;
    cronExpr: string;
    reportSession: string | null;
  }[];
  runs: OpsJobRun[];
}) {
  const jobName = (run: OpsJobRun) =>
    jobs.find((job) => job.id === run.jobId)?.jobName ??
    run.jobKey.split(":")[0] ??
    "unknown";

  return (
    <div className="table-wrap">
      <table className="data-table jobs-table">
        <thead>
          <tr>
            <th>任务 / 运行 ID</th>
            <th>调度</th>
            <th>开始时间（北京时间）</th>
            <th className="numeric">耗时</th>
            <th>结果</th>
            <th>摘要</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const meta = STATUS_META[run.status] ?? {
              label: run.status,
              tone: "warning",
            };
            return (
              <tr key={run.id}>
                <td>
                  <strong className="mono">{jobName(run)}</strong>
                  <small className="mono">{run.id.slice(0, 8)}</small>
                </td>
                <td>
                  {jobs.find((job) => job.id === run.jobId)?.cronExpr ?? "—"}
                </td>
                <td className="mono">
                  <Clock3 size={11} />
                  {formatShanghaiTime(run.startedAt)}
                </td>
                <td className="mono numeric">
                  {run.durationSeconds !== null
                    ? `${run.durationSeconds}s`
                    : "—"}
                </td>
                <td>
                  <span className={`run-result ${meta.tone}`}>
                    {meta.label === "SUCCESS" ? (
                      <CheckCircle2 size={11} />
                    ) : (
                      <TriangleAlert size={11} />
                    )}
                    {meta.label}
                    {run.errorClass ? ` · ${run.errorClass}` : ""}
                  </span>
                </td>
                <td>
                  {run.status === "FAILED"
                    ? "运行失败，需检查错误类别"
                    : jobName(run) === "intraday_tracking"
                      ? "盘中持仓波动核对；未达阈值不调用 AI"
                      : run.status.toLowerCase()}
                </td>
                <td>
                  <button
                    className="btn-ghost"
                    disabled
                    title="手动作业重跑接口尚未接入"
                  >
                    <RotateCcw size={12} />
                    重跑
                  </button>
                </td>
              </tr>
            );
          })}
          {runs.length === 0 ? (
            <tr>
              <td colSpan={7} className="muted">
                暂无作业运行记录
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

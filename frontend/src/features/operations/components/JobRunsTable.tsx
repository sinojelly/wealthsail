import { CheckCircle2, Clock3, RotateCcw, TriangleAlert } from "lucide-react";
import type { OpsJobRun, OpsResearchRun } from "../../../api/types";
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
  researchRuns = [],
}: {
  jobs: {
    id: string;
    jobName: string;
    cronExpr: string;
    reportSession: string | null;
  }[];
  runs: OpsJobRun[];
  researchRuns?: OpsResearchRun[];
}) {
  const jobName = (run: OpsJobRun) =>
    jobs.find((job) => job.id === run.jobId)?.jobName ??
    run.jobKey.split(":")[0] ??
    "unknown";

  return (
    <>
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
      {researchRuns.length > 0 && (
        <section aria-label="历史研究模拟入口">
          <h3>每日研究 · 历史模拟</h3>
          <div className="table-wrap">
            <table className="data-table jobs-table">
              <thead>
                <tr>
                  <th>原始研究 ID</th>
                  <th>开始时间（北京时间）</th>
                  <th>结果</th>
                  <th>冻结材料</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {researchRuns.map((run) => {
                  const available =
                    run.canPreview &&
                    ["READY", "FAILED"].includes(run.status) &&
                    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
                      run.runId,
                    );
                  return (
                    <tr key={run.runId}>
                      <td className="mono">{run.runId}</td>
                      <td>{formatShanghaiTime(run.startedAt)}</td>
                      <td>
                        {run.status}
                        {run.errorClass ? ` · ${run.errorClass}` : ""}
                      </td>
                      <td>
                        {available
                          ? "完整 · 按原验证时钟预览"
                          : "尚未具备完整预览材料"}
                      </td>
                      <td>
                        {available ? (
                          <a
                            className="btn-ghost"
                            href={`/operations/research-preview/${run.runId}`}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            模拟预览
                          </a>
                        ) : (
                          <button
                            className="btn-ghost"
                            disabled
                            title="仅已完成或失败且材料完整的研究可预览"
                          >
                            模拟预览
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}

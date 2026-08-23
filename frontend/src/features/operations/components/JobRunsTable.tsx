import { CheckCircle2, Clock3, RotateCcw, TriangleAlert } from "lucide-react";

const runs = [
  {
    id: "job-20260823-post",
    name: "report.postmarket.generate",
    schedule: "交易日 16:20",
    started: "16:20:03",
    duration: "42.8s",
    result: "SUCCESS",
    detail: "盘后复盘 v1 已发布",
  },
  {
    id: "job-20260823-fund",
    name: "report.fund_1430.generate",
    schedule: "交易日 14:30",
    started: "14:30:01",
    duration: "18.4s",
    result: "SUCCESS",
    detail: "2/2 基金已生成估值",
  },
  {
    id: "job-20260823-intra",
    name: "portfolio.intraday.track",
    schedule: "每 5 分钟",
    started: "14:25:00",
    duration: "3.7s",
    result: "SUCCESS",
    detail: "1 条风险触点已去重",
  },
  {
    id: "job-20260823-pre",
    name: "report.premarket.generate",
    schedule: "交易日 08:00",
    started: "08:00:02",
    duration: "31.2s",
    result: "WARNING",
    detail: "1 个海外宏观源延迟，使用上次成功快照",
  },
];

export default function JobRunsTable() {
  return (
    <div className="table-wrap">
      <table className="data-table jobs-table">
        <thead>
          <tr>
            <th>任务 / 运行 ID</th>
            <th>调度</th>
            <th>开始时间</th>
            <th className="numeric">耗时</th>
            <th>结果</th>
            <th>摘要</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>
                <strong className="mono">{run.name}</strong>
                <small className="mono">{run.id}</small>
              </td>
              <td>{run.schedule}</td>
              <td className="mono">
                <Clock3 size={11} />
                {run.started}
              </td>
              <td className="mono numeric">{run.duration}</td>
              <td>
                <span className={`run-result ${run.result.toLowerCase()}`}>
                  {run.result === "SUCCESS" ? (
                    <CheckCircle2 size={11} />
                  ) : (
                    <TriangleAlert size={11} />
                  )}
                  {run.result}
                </span>
              </td>
              <td>{run.detail}</td>
              <td>
                <button className="btn-ghost">
                  <RotateCcw size={12} />
                  重跑
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

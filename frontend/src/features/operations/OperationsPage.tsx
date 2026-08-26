import { useState } from "react";
import {
  Activity,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  Download,
  FileClock,
  ListChecks,
  RadioTower,
  ShieldAlert,
} from "lucide-react";
import { useOpsAudit, useOpsFreshness, useOpsJobs, useOpsOutbox } from "../../api/hooks";
import PageHeader from "../../components/layout/PageHeader";
import AuditLogTable from "./components/AuditLogTable";
import JobRunsTable from "./components/JobRunsTable";

type Tab = "jobs" | "audit";

export default function OperationsPage() {
  const [tab, setTab] = useState<Tab>("jobs");
  const jobs = useOpsJobs();
  const freshness = useOpsFreshness();
  const outbox = useOpsOutbox();
  const audit = useOpsAudit(50);

  const runs = jobs.data?.runs ?? [];
  const completed = runs.filter((run) => run.status === "COMPLETED").length;
  const successRate =
    runs.length > 0 ? ((completed / runs.length) * 100).toFixed(1) : "—";
  const pendingMessages =
    (outbox.data?.counts.pending ?? 0) + (outbox.data?.counts.sending ?? 0);
  const navFresh = freshness.data?.nav?.latestAsOf;
  const errors = [jobs.error, freshness.error, outbox.error, audit.error]
    .filter((error): error is Error => error instanceof Error)
    .map((error) => error.message);

  return (
    <>
      <PageHeader
        title="运维监控"
        description="作业运行、数据新鲜度、消息投递与不可变审计事件"
        actions={
          <button className="btn-secondary" disabled title="审计导出接口尚未接入">
            <Download size={14} />
            导出审计
          </button>
        }
      />
      {errors.length > 0 ? (
        <div className="alert alert-danger" role="alert">
          <ShieldAlert size={16} />
          <div>
            <strong>部分运维数据加载失败</strong>
            <p>{[...new Set(errors)].join("；")}</p>
          </div>
        </div>
      ) : null}
      <section className="ops-metrics">
        <div className="panel">
          <span className="ops-icon good">
            <CheckCircle2 size={17} />
          </span>
          <div>
            <span>作业成功率</span>
            <strong className="mono">
              {successRate === "—" ? "—" : `${successRate}%`}
            </strong>
            <small>
              {completed} / {runs.length} 次成功
            </small>
          </div>
        </div>
        <div className="panel">
          <span className="ops-icon">
            <Clock3 size={17} />
          </span>
          <div>
            <span>最近作业耗时</span>
            <strong className="mono">
              {runs[0]?.durationSeconds !== null &&
              runs[0]?.durationSeconds !== undefined
                ? `${runs[0].durationSeconds}s`
                : "—"}
            </strong>
            <small>按最近一次运行</small>
          </div>
        </div>
        <div className="panel">
          <span className="ops-icon good">
            <DatabaseZap size={17} />
          </span>
          <div>
            <span>净值快照新鲜度</span>
            <strong className="mono">{navFresh ?? "—"}</strong>
            <small>
              FRESH {freshness.data?.nav?.freshCount ?? 0} /{" "}
              {freshness.data?.nav?.total ?? 0}
            </small>
          </div>
        </div>
        <div className="panel">
          <span
            className={`ops-icon ${pendingMessages > 0 ? "warning" : "good"}`}
          >
            <RadioTower size={17} />
          </span>
          <div>
            <span>消息待投递</span>
            <strong className="mono">{pendingMessages}</strong>
            <small>
              已投递 {outbox.data?.counts.sent ?? 0} · DEAD{" "}
              {outbox.data?.counts.dead ?? 0}
            </small>
          </div>
        </div>
      </section>
      <div className="alert alert-warning">
        <ShieldAlert size={16} />
        <div>
          <strong>运维视图为脱敏数据</strong>
          <p>
            作业错误类别、消息队列状态与审计动作由 Domain API 清洗后返回，
            payload 与审计详情不经过浏览器。
          </p>
        </div>
      </div>
      <section className="panel operations-panel">
        <div className="ops-tabs" role="tablist" aria-label="运维视图">
          <button
            role="tab"
            aria-selected={tab === "jobs"}
            className={tab === "jobs" ? "active" : ""}
            onClick={() => setTab("jobs")}
          >
            <ListChecks size={14} />
            作业运行
          </button>
          <button
            role="tab"
            aria-selected={tab === "audit"}
            className={tab === "audit" ? "active" : ""}
            onClick={() => setTab("audit")}
          >
            <FileClock size={14} />
            审计日志
          </button>
        </div>
        {tab === "jobs" ? (
          <JobRunsTable jobs={jobs.data?.jobs ?? []} runs={runs} />
        ) : (
          <AuditLogTable rows={audit.data ?? []} />
        )}
      </section>
      <footer className="ops-footer">
        <Activity size={14} />
        <span>监控数据来自后端 Domain API · 自动刷新间隔 30 秒</span>
      </footer>
    </>
  );
}

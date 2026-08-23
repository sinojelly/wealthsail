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
import PageHeader from "../../components/layout/PageHeader";
import AuditLogTable from "./components/AuditLogTable";
import JobRunsTable from "./components/JobRunsTable";

type Tab = "jobs" | "audit";

export default function OperationsPage() {
  const [tab, setTab] = useState<Tab>("jobs");
  return (
    <>
      <PageHeader
        title="运维监控"
        description="作业运行、数据新鲜度、消息投递与不可变审计事件"
        actions={
          <button className="btn-secondary">
            <Download size={14} />
            导出审计
          </button>
        }
      />
      <section className="ops-metrics">
        <div className="panel">
          <span className="ops-icon good">
            <CheckCircle2 size={17} />
          </span>
          <div>
            <span>今日作业成功率</span>
            <strong className="mono">98.7%</strong>
            <small>76 / 77 次成功</small>
          </div>
        </div>
        <div className="panel">
          <span className="ops-icon">
            <Clock3 size={17} />
          </span>
          <div>
            <span>P95 作业耗时</span>
            <strong className="mono">42.8s</strong>
            <small>目标 ≤ 60s</small>
          </div>
        </div>
        <div className="panel">
          <span className="ops-icon good">
            <DatabaseZap size={17} />
          </span>
          <div>
            <span>行情数据新鲜度</span>
            <strong className="mono">8s</strong>
            <small>最后成功：16:00:08</small>
          </div>
        </div>
        <div className="panel">
          <span className="ops-icon warning">
            <RadioTower size={17} />
          </span>
          <div>
            <span>消息待投递</span>
            <strong className="mono">1</strong>
            <small>企业微信网关重试中</small>
          </div>
        </div>
      </section>
      <div className="alert alert-warning">
        <ShieldAlert size={16} />
        <div>
          <strong>盘前简报有 1 个降级数据源</strong>
          <p>
            海外宏观源超过 30 秒超时，作业使用上次成功快照并标记
            STALE；报告未把旧数据伪装成新数据。
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
        {tab === "jobs" ? <JobRunsTable /> : <AuditLogTable />}
      </section>
      <footer className="ops-footer">
        <Activity size={14} />
        <span>监控数据最后刷新：2026-08-23 23:10:08 · 自动刷新间隔 30 秒</span>
      </footer>
    </>
  );
}

import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Activity,
  Bot,
  Calculator,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  Download,
  FileClock,
  ListChecks,
  RadioTower,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import {
  useOpsAudit,
  useOpsFreshness,
  useOpsFundEstimate,
  useOpsJobs,
  useOpsOutbox,
  useRunFundEstimate,
} from "../../api/hooks";
import PageHeader from "../../components/layout/PageHeader";
import AuditLogTable from "./components/AuditLogTable";
import JobRunsTable from "./components/JobRunsTable";
import AiUsagePanel from "./components/AiUsagePanel";
import ResearchHistoryRepairs from "./components/ResearchHistoryRepairs";
import { formatShanghaiTime } from "./format-time";
import "./operations.css";

type Tab = "jobs" | "audit" | "ai-usage" | "research-repair";

export default function OperationsPage() {
  const [params] = useSearchParams();
  const [tab, setTab] = useState<Tab>(() =>
    params.get("tab") === "research-repair" ? "research-repair" : "jobs",
  );
  const jobs = useOpsJobs();
  const freshness = useOpsFreshness();
  const outbox = useOpsOutbox();
  const audit = useOpsAudit(50);
  const fundEstimate = useOpsFundEstimate();
  const runFundEstimate = useRunFundEstimate();

  const runs = jobs.data?.runs ?? [];
  const completed = runs.filter((run) => run.status === "COMPLETED").length;
  const successRate =
    runs.length > 0 ? ((completed / runs.length) * 100).toFixed(1) : "—";
  const pendingMessages =
    (outbox.data?.counts.pending ?? 0) + (outbox.data?.counts.sending ?? 0);
  const unknownMessages = outbox.data?.counts.unknown ?? 0;
  const deadMessages = outbox.data?.counts.dead ?? 0;
  const cancelledMessages = outbox.data?.counts.cancelled ?? 0;
  const deliveryNeedsAttention =
    pendingMessages + unknownMessages + deadMessages;
  const navFresh = freshness.data?.nav?.latestAsOf;
  const errors = [
    jobs.error,
    freshness.error,
    outbox.error,
    audit.error,
    fundEstimate.error,
    runFundEstimate.error,
  ]
    .filter((error): error is Error => error instanceof Error)
    .map((error) => error.message);
  const estimateStatus = fundEstimate.data;
  const estimateRunBusy = ["PENDING", "RUNNING"].includes(
    estimateStatus?.run?.status ?? "",
  );

  async function requestFundEstimate() {
    try {
      await runFundEstimate.mutateAsync();
    } catch {
      // Mutation error is rendered through the unified alert above.
    }
  }

  return (
    <div className="operations-page">
      <PageHeader
        title="运维监控"
        description="作业运行、AI 用量、数据新鲜度与审计事件 · 所有时间均为北京时间"
        actions={
          <button
            className="btn-secondary"
            disabled
            title="审计导出接口尚未接入"
          >
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
      <section className="panel fund-estimate-ops-card">
        <div className="fund-estimate-ops-heading">
          <span className="ops-icon good">
            <Calculator size={17} />
          </span>
          <div>
            <h2>场外基金盘中估值</h2>
            <p>
              Worker 使用最近官方净值和基金披露成分计算；API
              不直接访问行情供应商。
            </p>
          </div>
          <button
            className="btn-primary"
            type="button"
            disabled={
              !estimateStatus?.canRun ||
              estimateRunBusy ||
              runFundEstimate.isPending
            }
            onClick={() => void requestFundEstimate()}
          >
            <RefreshCw
              size={14}
              className={estimateRunBusy ? "spin" : undefined}
            />
            {runFundEstimate.isPending || estimateRunBusy
              ? "基金估值处理中"
              : "立即计算基金估值"}
          </button>
        </div>
        <div className="fund-estimate-ops-summary">
          <div>
            <span>估值覆盖</span>
            <strong>
              {estimateStatus
                ? `${estimateStatus.coverage.estimatedCount} / ${estimateStatus.coverage.heldFundCount} 只已生成`
                : "—"}
            </strong>
            <small>
              {estimateStatus
                ? `${estimateStatus.coverage.unavailableCount} 只因覆盖不足而弃权`
                : "正在读取状态"}
            </small>
          </div>
          <div>
            <span>最近手动任务</span>
            <strong>{estimateStatus?.run?.status ?? "尚未运行"}</strong>
            <small>
              {estimateStatus?.run?.errorClass
                ? `错误：${estimateStatus.run.errorClass}`
                : estimateStatus?.coverage.latestAsOf
                  ? `最近估值 ${formatShanghaiTime(estimateStatus.coverage.latestAsOf)}`
                  : "暂无盘中估值快照"}
            </small>
          </div>
          <div>
            <span>交易日状态</span>
            <strong>
              {estimateStatus?.calendar.isTradingDay ? "交易日" : "非交易日"}
            </strong>
            <small>
              最近交易日{" "}
              {estimateStatus?.calendar.latestTradingDate ?? "待确认"}
            </small>
          </div>
        </div>
        {estimateStatus?.calendar.status !== "READY" ? (
          <div className="fund-estimate-calendar-warning" role="status">
            <ShieldAlert size={14} />
            <span>
              交易日历尚未补齐；当前已按已落库的最新官方净值日安全兜底，Worker
              会在启动时自动重试补齐。
            </span>
          </div>
        ) : null}
        {estimateStatus && !estimateStatus.canRun ? (
          <p className="fund-estimate-nontrading-note">
            今天不是交易日，无需生成盘中估值；持仓继续使用最近官方净值。
          </p>
        ) : null}
      </section>
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
            <strong className="mono">
              {navFresh ? formatShanghaiTime(navFresh) : "—"}
            </strong>
            <small>
              FRESH {freshness.data?.nav?.freshCount ?? 0} /{" "}
              {freshness.data?.nav?.total ?? 0}
            </small>
          </div>
        </div>
        <div className="panel">
          <span
            className={`ops-icon ${deliveryNeedsAttention > 0 ? "warning" : "good"}`}
          >
            <RadioTower size={17} />
          </span>
          <div>
            <span>消息待投递</span>
            <strong className="mono">{pendingMessages}</strong>
            <small>
              已投递 {outbox.data?.counts.sent ?? 0} · UNKNOWN {unknownMessages}{" "}
              · DEAD {deadMessages} · CANCELLED {cancelledMessages}
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
          <button
            role="tab"
            aria-selected={tab === "ai-usage"}
            className={tab === "ai-usage" ? "active" : ""}
            onClick={() => setTab("ai-usage")}
          >
            <Bot size={14} />
            AI 用量
          </button>
          <button
            role="tab"
            aria-selected={tab === "research-repair"}
            className={tab === "research-repair" ? "active" : ""}
            onClick={() => setTab("research-repair")}
          >
            <FileClock size={14} />
            历史研究修复
          </button>
        </div>
        {tab === "jobs" ? (
          <JobRunsTable
            jobs={jobs.data?.jobs ?? []}
            runs={runs}
            researchRuns={jobs.data?.researchRuns ?? []}
          />
        ) : tab === "audit" ? (
          <AuditLogTable rows={audit.data ?? []} />
        ) : tab === "research-repair" ? (
          <ResearchHistoryRepairs
            sourceRunId={params.get("sourceRunId") ?? undefined}
          />
        ) : (
          <AiUsagePanel />
        )}
      </section>
      <footer className="ops-footer">
        <Activity size={14} />
        <span>
          监控数据来自后端 Domain API · 任务列表约每 30 秒刷新，AI 账户约每 180
          秒刷新
        </span>
      </footer>
    </div>
  );
}

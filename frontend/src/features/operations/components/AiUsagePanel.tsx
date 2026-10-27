import { useState } from "react";
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import {
  useAiUsage,
  useAiAccount,
  type AiAccount,
  type AiQuota,
  type AiTokens,
  type AiUsageTask,
} from "../../../api/ai-usage";
import { HttpError } from "../../../api/client";
import { formatShanghaiTime } from "../format-time";

const statuses: Record<string, { label: string; tone: string }> = {
  SUCCEEDED: { label: "成功", tone: "success" },
  FAILED: { label: "失败", tone: "failed" },
  PARTIAL: { label: "部分成功", tone: "warning" },
  RUNNING: { label: "执行中", tone: "warning" },
  UNCERTAIN: { label: "结果不确定", tone: "warning" },
  NONE: { label: "未调用", tone: "neutral" },
  DEGRADED: { label: "降级完成", tone: "warning" },
  CANCELLED: { label: "已取消", tone: "neutral" },
};
const taskTypeLabels: Record<string, string> = {
  DAILY_RESEARCH: "日常投研",
  REPORT: "研报生成",
  CHAT: "智能问答",
};
const callTypeLabels: Record<string, string> = {
  research: "研究推理",
  completion: "文本生成",
  chat: "对话推理",
};
const quotaReasons: Record<string, string> = {
  UNKNOWN: "未知",
  RESET_OR_TOPUP: "重置或充值，无法估算",
  PERIOD_CHANGED: "周期变化，无法估算",
  ACCOUNT_CHANGED: "账户变化，无法估算",
  CONCURRENT: "并发调用，无法归因",
  INCOMPLETE: "快照不完整，无法估算",
};
const accountMessages: Record<string, string> = {
  READY: "账户可用",
  STALE: "本次账户查询失败，以下为最后一次成功观测的历史快照。",
  UNAVAILABLE: "账户信息暂不可用，历史记录仍可查看。",
  UNAUTHENTICATED: "Codex 尚未登录或认证已失效。",
  NOT_CONFIGURED: "尚未配置 Codex 账户。",
  RESTRICTED: "仅平台管理员可查看共享 AI 账户，当前显示本租户历史。",
};
const limitationMessages: Record<string, string> = {
  ACCOUNT_DELTA_ESTIMATE:
    "用量根据推理前后的账户额度变化估算，外部并发可能影响归因。",
  HISTORY_FROM_INSTALLATION: "历史从启用采集后开始记录，不回填旧任务的消耗。",
  USAGE_LEDGER_NOT_MIGRATED: "用量记录尚未初始化，请先完成数据库升级。",
  QUOTA_UNAVAILABLE: "供应商暂未提供可用的额度窗口信息。",
  CREDITS_UNAVAILABLE: "供应商暂未提供 credits 余额，不视为零余额。",
  ACCOUNT_IDENTITY_UNAVAILABLE:
    "供应商未提供可核对的账户标识，无法可靠归因额度变化。",
  ACCOUNT_UNAVAILABLE: "当前无法读取供应商账户信息，请稍后刷新。",
  ACCOUNT_UNAUTHENTICATED: "Codex 尚未登录或认证已失效，请联系运维处理。",
  CODEX_NOT_CONFIGURED: "尚未配置 Codex 账户，请联系运维处理。",
  ACCOUNT_READ_FAILED: "账户信息读取失败，历史任务仍可查看。",
  ACCOUNT_STALE: "历史额度仅供参考，请刷新后确认最新状态。",
  ACCOUNT_AUTH_CHANGED:
    "Codex 认证已变更，旧账户快照已清除；请刷新或等待新的账户观测。",
  PLATFORM_ADMIN_REQUIRED: "共享账户详情仅平台管理员可见。",
};

function limitationMessage(note: string) {
  return (
    limitationMessages[note] ??
    (/^[A-Z][A-Z0-9_]+$/.test(note) ? "供应商未提供完整的账户用量信息。" : note)
  );
}

function nonnegative(value: number | null): value is number {
  return value !== null && Number.isFinite(value) && value >= 0;
}

function credits(value: string | null): string | null {
  return value !== null &&
    /^\d+(?:\.\d+)?$/.test(value) &&
    Number.isFinite(Number(value))
    ? value
    : null;
}

function Status({ value }: { value: string }) {
  const meta = statuses[value] ?? { label: "未知", tone: "neutral" };
  return <span className={`ai-status ${meta.tone}`}>{meta.label}</span>;
}

function Tokens({ row }: { row: AiTokens }) {
  return (
    <dl className="ai-tokens">
      {(
        [
          ["输入", row.inputTokens],
          ["缓存", row.cachedTokens],
          ["输出", row.outputTokens],
          ["总计", row.totalTokens],
        ] as const
      ).map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd className="mono">
            {nonnegative(value) ? value.toLocaleString("en-US") : "未知"}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Quota({ value }: { value: AiQuota }) {
  return (
    <div className="ai-quota">
      {value.windows.map((window) => (
        <div key={window.key}>
          <span>{window.label}</span>
          <strong>
            {window.status === "ESTIMATED" &&
            nonnegative(window.consumedPercent) &&
            window.consumedPercent <= 100
              ? `${window.consumedPercent} 百分点（估算）`
              : (quotaReasons[window.status] ?? "未知")}
          </strong>
        </div>
      ))}
      <div>
        <span>Credits</span>
        <strong>
          {value.credits.status === "ESTIMATED" &&
          credits(value.credits.consumed) !== null
            ? `${value.credits.consumed} credits（估算）`
            : (quotaReasons[value.credits.status] ?? "未知")}
        </strong>
      </div>
    </div>
  );
}

function Account({ account }: { account: AiAccount }) {
  const balance = credits(account.credits?.balance ?? null);
  return (
    <section className="ai-account" aria-label="当前 AI 账户">
      <div className="ai-section-heading">
        <h2>当前 Codex 账户</h2>
        <span
          className={`ai-status ${account.status === "READY" ? "success" : "warning"}`}
        >
          {account.status === "READY"
            ? "可用"
            : account.status === "STALE"
              ? "历史快照"
              : "待确认"}
        </span>
      </div>
      {account.status !== "READY" ? (
        <p className="ai-account-message">
          {accountMessages[account.status] ?? "账户状态未知"}
        </p>
      ) : null}
      {account.status === "READY" || account.status === "STALE" ? (
        <>
          <dl className="ai-account-meta">
            <div>
              <dt>当前账户</dt>
              <dd>{account.email ?? "未知"}</dd>
            </div>
            <div>
              <dt>会员类型</dt>
              <dd>{account.planType ?? "未知"}</dd>
            </div>
            <div>
              <dt>认证方式</dt>
              <dd>{account.authType ?? "未知"}</dd>
            </div>
            <div>
              <dt>Credits 余额</dt>
              <dd>
                {account.credits?.unlimited
                  ? "不限额（供应商标记）"
                  : balance !== null
                    ? `${balance} credits`
                    : "未知"}
              </dd>
            </div>
          </dl>
          <div className="ai-windows">
            {account.windows.map((window) => {
              const remaining =
                nonnegative(window.remainingPercent) &&
                window.remainingPercent <= 100
                  ? window.remainingPercent
                  : null;
              return (
                <div className="ai-window" key={window.key}>
                  <span>{window.label}</span>
                  <div>
                    <strong>
                      {remaining === null ? "未知" : `${remaining}%`}
                    </strong>
                    <small>剩余</small>
                  </div>
                  <div className="ai-quota-bar" aria-hidden="true">
                    <span style={{ width: `${remaining ?? 0}%` }} />
                  </div>
                  <small>
                    下次重置{" "}
                    {formatShanghaiTime(
                      window.resetsAt === null ? null : window.resetsAt * 1000,
                    )}
                  </small>
                </div>
              );
            })}
          </div>
          <p className="ai-note">
            {account.status === "STALE" ? "最后成功观测时间" : "观测时间"}{" "}
            {formatShanghaiTime(account.observedAt)} · 北京时间
            {account.status === "READY"
              ? " · 近期观测（服务端缓存最长 180 秒）"
              : ""}
          </p>
        </>
      ) : null}
      {account.limitations.map((note) => (
        <p className="ai-note" key={note}>
          {limitationMessage(note)}
        </p>
      ))}
    </section>
  );
}

function Task({ task }: { task: AiUsageTask }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <tr>
        <td>
          <strong>{task.taskName}</strong>
          <small title={task.taskType}>
            {taskTypeLabels[task.taskType] ?? "未知任务类型"}
          </small>
          <small className="mono" title={task.id}>
            {task.id.slice(0, 8)}
          </small>
        </td>
        <td className="ai-time">
          <time>{formatShanghaiTime(task.startedAt)}</time>
          <small>
            结束{" "}
            {task.finishedAt ? formatShanghaiTime(task.finishedAt) : "尚未结束"}
          </small>
        </td>
        <td>
          <Status value={task.inferenceStatus} />
        </td>
        <td>
          <Status value={task.businessStatus} />
        </td>
        <td>{task.models.length ? task.models.join(" / ") : "未调用"}</td>
        <td>
          <Tokens row={task} />
        </td>
        <td>
          <Quota value={task.quota} />
        </td>
        <td>
          <button
            type="button"
            className="btn-ghost ai-expand"
            aria-expanded={expanded}
            aria-label={`${expanded ? "收起" : "展开"} ${task.taskName} ${task.callCount} 次推理`}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            {task.callCount} 次推理
          </button>
        </td>
      </tr>
      {expanded ? (
        <tr className="ai-calls-row">
          <td colSpan={8}>
            <section
              aria-label={`${task.taskName}推理详情`}
              className="ai-calls"
            >
              <h3>任务内推理明细</h3>
              {task.calls.length === 0 ? (
                <p>此任务没有采集到推理记录。</p>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>模型 / 调用类型</th>
                      <th>时间（北京时间）</th>
                      <th>推理结果</th>
                      <th>Token 输入 / 缓存 / 输出 / 总计</th>
                      <th>额度变化</th>
                    </tr>
                  </thead>
                  <tbody>
                    {task.calls.map((call) => (
                      <tr key={call.id}>
                        <td>
                          <strong>{call.model}</strong>
                          <small title={call.callType}>
                            {callTypeLabels[call.callType] ?? "未知调用类型"}
                          </small>
                        </td>
                        <td className="ai-time">
                          {formatShanghaiTime(call.startedAt)}
                          <small>
                            结束{" "}
                            {call.finishedAt
                              ? formatShanghaiTime(call.finishedAt)
                              : "尚未结束"}
                          </small>
                        </td>
                        <td>
                          <Status value={call.status} />
                          {call.errorClass ? (
                            <small className="ai-error-class">
                              {call.errorClass}
                            </small>
                          ) : null}
                        </td>
                        <td>
                          <Tokens row={call} />
                        </td>
                        <td>
                          <Quota value={call.quota} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </td>
        </tr>
      ) : null}
    </>
  );
}

export default function AiUsagePanel() {
  const [offset, setOffset] = useState(0);
  const usage = useAiUsage(true, offset);
  const accountQuery = useAiAccount(true);
  const data = usage.data;
  const accountAccessDenied =
    accountQuery.error instanceof HttpError &&
    [401, 403].includes(accountQuery.error.status);
  const account = accountAccessDenied ? undefined : accountQuery.data;
  const displayedAccount: AiAccount | undefined =
    accountQuery.error && account && ["READY", "STALE"].includes(account.status)
      ? { ...account, status: "STALE" }
      : account;
  const refreshing = usage.isFetching || accountQuery.isFetching;

  function refreshAll() {
    void usage.refetch();
    accountQuery.refresh();
  }
  return (
    <div className="ai-usage-panel" role="tabpanel" aria-label="AI 用量">
      <div className="ai-section-heading">
        <div>
          <h2>AI 用量跟踪</h2>
          <p>按业务任务汇总消耗，展开查看每次模型推理。</p>
        </div>
        <button
          type="button"
          className="btn-secondary"
          aria-label="刷新 AI 用量"
          disabled={refreshing}
          onClick={refreshAll}
        >
          <RefreshCw size={14} className={refreshing ? "spin" : undefined} />
          刷新
        </button>
      </div>
      {accountQuery.isLoading ? (
        <p role="status" aria-label="AI 账户加载中">
          正在读取 Codex 账户信息，历史任务独立加载…
        </p>
      ) : null}
      {accountQuery.error ? (
        <div
          role="alert"
          aria-label="AI 账户加载失败"
          className="alert alert-warning"
        >
          <div>
            <strong>AI 账户信息加载失败</strong>
            <p>账户查询暂不可用，历史任务仍可查看。</p>
            <button
              type="button"
              className="btn-secondary"
              disabled={accountQuery.isFetching}
              onClick={accountQuery.refresh}
            >
              重试账户信息
            </button>
          </div>
        </div>
      ) : null}
      {displayedAccount ? <Account account={displayedAccount} /> : null}
      {usage.isLoading ? (
        <p role="status" aria-label="AI 用量加载中">
          正在读取 AI 用量…
        </p>
      ) : null}
      {usage.error ? (
        <div
          role="alert"
          aria-label="AI 用量加载失败"
          className="alert alert-danger"
        >
          <div>
            <strong>AI 用量加载失败</strong>
            <p>{usage.error.message}</p>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void usage.refetch()}
            >
              重试 AI 用量
            </button>
          </div>
        </div>
      ) : null}
      {data ? (
        <>
          <aside className="ai-usage-explanation">
            <strong>用量为账户额度变化估算</strong>
            <p>
              百分点和 credits
              均非人民币金额；外部并发、额度精度和供应商延迟可能影响归因。窗口分别计量，不相加。
            </p>
            <p>
              缓存输入已包含在输入 token 中，不重复相加；缺失数据显示「未知」。
            </p>
            {data.limitations.map((note) => (
              <p key={note}>{limitationMessage(note)}</p>
            ))}
          </aside>
          <div className="ai-section-heading">
            <h2>历史任务</h2>
            <span className="ai-note">共 {data.total} 个任务 · 每页 20 个</span>
          </div>
          {data.tasks.length > 0 ? (
            <p className="ai-table-hint">
              左右滑动表格查看 token、用量和推理明细。
            </p>
          ) : null}
          {data.tasks.length > 0 ? (
            <div className="table-wrap">
              <table
                className="data-table ai-tasks-table"
                aria-label="AI 用量历史任务"
              >
                <thead>
                  <tr>
                    <th>任务名称 / 类型</th>
                    <th>时间（北京时间）</th>
                    <th>推理结果</th>
                    <th>业务结果</th>
                    <th>模型</th>
                    <th>Token 输入 / 缓存 / 输出 / 总计</th>
                    <th>用量估算</th>
                    <th>调用明细</th>
                  </tr>
                </thead>
                <tbody>
                  {data.tasks.map((task) => (
                    <Task key={task.id} task={task} />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="ai-empty">
              <strong>尚无 AI 用量记录</strong>
              <p>从启用采集后的任务开始记录；旧任务没有采集数据时不回填。</p>
            </div>
          )}
          <nav className="ai-pagination" aria-label="AI 用量分页">
            <span>
              {data.total > 0
                ? `${data.offset + 1}–${Math.min(data.offset + data.tasks.length, data.total)} / ${data.total}`
                : "0 个任务"}
            </span>
            <button
              type="button"
              className="btn-secondary"
              disabled={offset === 0 || usage.isFetching}
              onClick={() => setOffset(Math.max(0, offset - 20))}
            >
              上一页
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={offset + 20 >= data.total || usage.isFetching}
              onClick={() => setOffset(offset + 20)}
            >
              下一页
            </button>
          </nav>
        </>
      ) : null}
    </div>
  );
}

import type { CodexDiagnostic } from "../../../api/ai-usage";
import { runtimeFailureReasons } from "../../../api/runtime-diagnostics";

const reasons: Readonly<Record<string, string>> = runtimeFailureReasons;
const stages: Record<string, string> = {
  CLI_EXECUTION: "模型执行",
  CLI_STDOUT: "模型执行事件",
  CLI_STDERR: "CLI 错误输出",
  CLI_OUTPUT: "模型输出处理",
  RUNNER_HTTP: "执行器 HTTP 通信",
  REQUEST: "执行请求",
  SPAWN: "启动账户进程",
  INITIALIZE: "账户初始化",
  ACCOUNT_READ: "账户读取",
  RATE_LIMITS_READ: "额度读取",
  CLEANUP: "进程清理",
  SNAPSHOT: "账户快照",
  TRANSPORT: "执行器通信",
  CACHE_FETCH: "账户刷新",
};

function integer(
  value: number | undefined,
  low: number,
  high: number,
): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= low &&
    value <= high
  );
}

export default function CodexDiagnosticDetails({
  value,
  label,
}: {
  value?: CodexDiagnostic;
  label: string;
}) {
  if (
    !value ||
    value.version !== "codex-diagnostic-v1" ||
    !["CLI", "RUNNER", "TRANSPORT", "ACCOUNT_RPC", "CLIENT"].includes(
      value.origin,
    ) ||
    !Object.hasOwn(reasons, value.reasonCode) ||
    !Object.hasOwn(stages, value.stage)
  )
    return null;
  const codes = [
    integer(value.httpStatus, 100, 599) ? `HTTP ${value.httpStatus}` : null,
    integer(value.rpcCode, -32768, -32000) ? `RPC ${value.rpcCode}` : null,
    integer(value.exitCode, -2147483648, 2147483647)
      ? `退出码 ${value.exitCode}`
      : null,
  ].filter(Boolean);
  return (
    <div className="ai-failure-diagnostic" role="group" aria-label={label}>
      <strong title={value.reasonCode}>{reasons[value.reasonCode]}</strong>
      <small>{[stages[value.stage], ...codes].join(" · ")}</small>
    </div>
  );
}

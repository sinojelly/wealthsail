import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../../../api/daily-research-debug";
import { HttpError } from "../../../api/client";
import type { DailyResearch } from "../../../api/investment";
import { timeLabel } from "../macro-display";
import { DailyResearchFailureDiagnosis } from "./DailyResearchReport";
import "../research-header.css";

const LIMIT = 35 * 60 * 1000;
const active = (status?: string) => status === "QUEUED" || status === "RUNNING";
const pending = (request?: api.DebugResearchRequest | null) =>
  active(request?.status) ||
  (request?.status === "UNCERTAIN" && request.completedAt == null);
const commandStorageKey = (actor: string) =>
  `wealthsail:manual-research:command:${actor}`;
function readCommand(actor: string) {
  try {
    const value = sessionStorage.getItem(commandStorageKey(actor));
    return value &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
        value,
      )
      ? value
      : null;
  } catch {
    return null;
  }
}
function storeCommand(actor: string, value: string | null) {
  try {
    if (value) sessionStorage.setItem(commandStorageKey(actor), value);
    else sessionStorage.removeItem(commandStorageKey(actor));
  } catch {
    // Storage restrictions must not prevent an explicit request or its acknowledgment.
  }
}
interface Props {
  actor: string;
  onReady?: () => void;
  currentResearch?: DailyResearch | null;
}
export default function DailyResearchRefresh(props: Props) {
  return <RefreshControl key={props.actor} {...props} />;
}
function ManualRequestRecord({
  request,
  historical,
  children,
}: {
  request?: api.DebugResearchRequest | null;
  historical: boolean;
  children: ReactNode;
}) {
  const content = (
    <>
      {request && (
        <div className="op-research-request-time" aria-label="最近手动请求身份">
          <p>
            最近手动请求 · 来源：
            {request.requestMode === "DEBUG"
              ? "手动调试"
              : request.requestMode === "MANUAL"
                ? "手动启动"
                : "手动请求（模式未记录）"}
          </p>
          <p>请求ID：{request.id}</p>
          <p>提交时间：{timeLabel(request.requestedAt)} 北京时间</p>
          <p>
            开始：{timeLabel(request.startedAt)} · 结束：
            {timeLabel(request.completedAt)} 北京时间
          </p>
        </div>
      )}
      {children}
    </>
  );
  return historical ? (
    <details className="op-research-manual-history">
      <summary>
        最近手动请求（历史） · 提交于 {timeLabel(request?.requestedAt)} 北京时间
      </summary>
      <p>
        此记录属于较早的手动请求；最新研究的状态与诊断见上方“执行与调度详情”。
      </p>
      {content}
    </details>
  ) : (
    content
  );
}
function RefreshControl({ actor, onReady, currentResearch }: Props) {
  const client = useQueryClient();
  const [unconfirmedKey, setUnconfirmedKey] = useState(() =>
    readCommand(actor),
  );
  const commandKey = useRef<string | null>(unconfirmedKey);
  const completed = useRef<string | null>(null);
  const failedContextsReloaded = useRef(new Set<string>());
  const [submitted, setSubmitted] = useState<api.DebugResearchRequest | null>(
    null,
  );
  const [openedAt] = useState(() => Date.now());
  const [timedOut, setTimedOut] = useState<string | null>(null);
  const capability = useQuery({
    queryKey: [actor, "daily-research-debug"],
    queryFn: ({ signal }) => api.getResearchRefresh(signal),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const current = submitted ?? capability.data?.request;
  const status = useQuery({
    queryKey: [actor, "daily-research-debug-status", current?.id],
    queryFn: ({ signal }) => api.getResearchRefreshStatus(current!.id, signal),
    enabled: capability.data?.enabled === true && !!current && pending(current),
    refetchInterval: (query) => {
      const value = query.state.data ?? current;
      return active(value?.status) ? 3000 : pending(value) ? 15000 : false;
    },
    retry: false,
    refetchOnWindowFocus: true,
  });
  const result = status.data?.id === current?.id ? status.data : current;
  const started = Date.parse(result?.startedAt ?? "");
  const expired =
    !!result &&
    active(result.status) &&
    Number.isFinite(started) &&
    (openedAt - started >= LIMIT || timedOut === result.id);
  const busy = pending(result);
  useEffect(() => {
    if (!current || !active(result?.status) || !Number.isFinite(started))
      return;
    const timer = setTimeout(
      () => setTimedOut(current.id),
      Math.max(0, started + LIMIT - Date.now()),
    );
    return () => clearTimeout(timer);
  }, [current, started, result?.status]);
  useEffect(() => {
    if (result?.status !== "READY" || completed.current === result.id) return;
    completed.current = result.id;
    void client.invalidateQueries({
      predicate: (query) =>
        query.queryKey[0] === actor &&
        [
          "opportunity-context",
          "market-context",
          "analysis",
          "research-funds",
        ].some((key) => query.queryKey.includes(key)),
    });
    onReady?.();
  }, [result, actor, client, onReady]);
  useEffect(() => {
    if (
      !result ||
      result.id !== current?.id ||
      failedContextsReloaded.current.has(result.id) ||
      !(
        result.status === "FAILED" ||
        (result.status === "UNCERTAIN" &&
          result.completedAt != null &&
          Number.isFinite(Date.parse(result.completedAt)))
      )
    )
      return;
    failedContextsReloaded.current.add(result.id);
    void client.invalidateQueries({
      predicate: (query) =>
        query.queryKey[0] === actor &&
        ["opportunity-context", "market-context"].some((key) =>
          query.queryKey.includes(key),
        ),
    });
  }, [result, current?.id, actor, client]);
  const submit = useMutation({
    mutationFn: (key: string) => {
      commandKey.current = key;
      storeCommand(actor, key);
      setUnconfirmedKey(key);
      return api.startResearchRefresh(key);
    },
    retry: false,
    onSuccess: (value, key) => {
      setSubmitted(value);
      if (readCommand(actor) === key) storeCommand(actor, null);
      if (commandKey.current === key) {
        commandKey.current = null;
        setUnconfirmedKey(null);
      }
    },
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api.cancelResearchRefresh(id),
    retry: false,
    onSuccess: (value) => {
      setSubmitted(value);
      client.setQueryData(
        [actor, "daily-research-debug-status", value.id],
        value,
      );
    },
    onError: () => {
      void status.refetch();
    },
  });
  const phases: Record<string, string> = {
    COLLECTING: "正在采集公共数据",
    WAITING_MODEL_SLOT: "等待模型槽，尚未调用 AI",
    MODEL_RUNNING: "AI 分析中",
    VERIFYING_OUTPUT: "正在核对引用来源",
    REPORT_VALIDATION: "报告验证中",
    PERSISTING: "正在保存报告",
  };
  const exitEvidence: Record<string, string> = {
    LOCAL_RUNNING: "本机研究进程仍在运行，需要等待退出确认",
    LOCAL_EXIT_CONFIRMED: "已取得本机退出证明，等待释放模型槽",
    LOCAL_EXIT_UNCONFIRMED: "本机退出证明仍不完整，需要受控恢复",
    LOCAL_EVIDENCE_NOT_RETAINED: "旧研究退出证据未保留，需要本机受控维护",
    LOCAL_PLATFORM_UNSUPPORTED: "本地退出证明不可用，需要受控维护",
  };
  const canClearUnconfirmed =
    !!unconfirmedKey &&
    submit.error instanceof HttpError &&
    submit.error.status === 403 &&
    submit.error.code === "FORBIDDEN";
  if (capability.data?.enabled !== true) return null;
  const safeError =
    result?.errorClass &&
    /^[A-Za-z][A-Za-z0-9_.]{0,79}$/.test(result.errorClass)
      ? result.errorClass
      : "原因待核查";
  const currentRunId = currentResearch?.pipeline
    ? currentResearch.pipeline.researchRunId
    : currentResearch?.runId;
  const currentStartedAt = Date.parse(
    (currentResearch?.pipeline
      ? currentResearch.pipeline.startedAt
      : currentResearch?.startedAt) ?? "",
  );
  const requestEndedAt = Date.parse(
    result?.completedAt ?? result?.startedAt ?? result?.requestedAt ?? "",
  );
  const historical =
    !!result &&
    !busy &&
    !(currentRunId && currentRunId === result.reportRunId) &&
    Number.isFinite(currentStartedAt) &&
    Number.isFinite(requestEndedAt) &&
    currentStartedAt > requestEndedAt;
  return (
    <section
      className="op-debug-refresh"
      aria-label="手动研究"
      data-state={result?.status}
    >
      <div className="op-research-refresh-action">
        <button
          type="button"
          disabled={busy || submit.isPending || cancel.isPending}
          onClick={() =>
            submit.mutate(commandKey.current ?? crypto.randomUUID())
          }
        >
          {unconfirmedKey ? "重试本次提交" : "手动启动研究"}
        </button>
        <small>
          将重新采集最新公共信息并调用一次
          Codex，产生新的用量；普通刷新不会重跑。
        </small>
      </div>
      {result?.status === "QUEUED" && result.canCancel === true && (
        <button
          type="button"
          disabled={cancel.isPending}
          onClick={() => cancel.mutate(result.id)}
        >
          {cancel.isPending ? "正在取消排队…" : "取消排队研究"}
        </button>
      )}
      {cancel.isError && (
        <p role="alert">
          取消未生效，已重新查询状态；若研究已开始则不能取消排队。不会重新提交研究。
        </p>
      )}
      {unconfirmedKey && !submit.isPending && (
        <p role="alert">
          提交结果待确认，可重试本次提交；相同请求不会重复运行。
        </p>
      )}
      {canClearUnconfirmed && (
        <div>
          <button
            type="button"
            onClick={() => {
              commandKey.current = null;
              storeCommand(actor, null);
              setUnconfirmedKey(null);
              submit.reset();
            }}
          >
            清除未确认提交记录
          </button>
          <p>只清除此页记录，不会取消后台任务。下一次启动将使用新请求。</p>
        </div>
      )}
      <ManualRequestRecord request={result} historical={historical}>
        {expired && active(result?.status) ? (
          <p role="status">
            超过35分钟，执行结果待确认，正在继续查询后台结果。保留上一份有效报告，本次执行结束前不会启动新的模型。
          </p>
        ) : result?.status === "QUEUED" ? (
          <p role="status">
            排队等待模型槽 ·{" "}
            {result.blockedReasonCode === "MODEL_GUARD_ACTIVE"
              ? "上次研究仍占用模型槽，需要等待退出确认"
              : result.blockedReasonCode === "REQUEST_RUNNING"
                ? "另一次研究正在运行"
                : "等待后台认领"}
            。尚未采集或调用 AI，保留上一份有效报告。
            排队最多5分钟，超时终止且不自动重试。
            {result.queueExpiresAt && (
              <> 排队截止：{timeLabel(result.queueExpiresAt)}。</>
            )}
          </p>
        ) : result?.status === "RUNNING" ? (
          <p role="status">
            {phases[result.executionStage ?? ""] ?? "后台处理中 · 阶段待确认"}
            。保留上一份有效报告。
          </p>
        ) : result?.status === "CANCELLED" ? (
          <p role="status">
            排队研究已取消，记录已保留，未调用
            AI。不会自动重试；需要研究时请重新点击。
          </p>
        ) : result?.status === "EXPIRED" ? (
          <p role="status">
            {result.errorClass === "QueueInvalidatedByMaintenance" ||
            result.errorClass === "LocalRebootQueueExpired"
              ? "旧排队请求已在本机维护中终止"
              : "排队等待超过5分钟，已终止"}
            ；未开始采集或调用 AI。不会自动重试；需要研究时请重新点击。
          </p>
        ) : result?.status === "READY" ? (
          <p role="status">手动研究已完成，页面将加载最新有效报告。</p>
        ) : result?.status === "FAILED" ? (
          <p role="alert">
            采集或分析失败：{safeError}。保留上一份有效报告，可重新运行。
          </p>
        ) : result?.status === "UNCERTAIN" ? (
          <p role="status">
            执行结果待确认：{safeError}。
            {result.completedAt
              ? "本次执行已结束，保留上一份有效报告，可显式提交新请求；模型退出尚未确认时，新请求将等待。"
              : "正在继续查询后台结果，保留上一份有效报告，不会启动新的模型。"}
          </p>
        ) : null}
        {result?.status === "QUEUED" &&
          result.blockedReasonCode === "MODEL_GUARD_ACTIVE" && (
            <div>
              {result.modelExitEvidenceCheckedAt &&
                exitEvidence[result.modelExitEvidenceState ?? ""] && (
                  <p>
                    {exitEvidence[result.modelExitEvidenceState!]}（核对于{" "}
                    {timeLabel(result.modelExitEvidenceCheckedAt)}）。
                  </p>
                )}
              <p>
                若原研究已结束仍长期阻塞，请在运行研究的 Windows
                主机执行本地受控恢复。等待超时不会释放旧锁；不要按进程名结束全部
                Codex。
              </p>
            </div>
          )}
        {(result?.status === "FAILED" || result?.status === "UNCERTAIN") && (
          <details className="op-research-execution-diagnostics">
            <summary>查看执行诊断</summary>
            <DailyResearchFailureDiagnosis
              data={{
                failureStage: result.failureStage,
                failureReasonCode: result.failureReasonCode,
                failureRuleId: result.failureRuleId,
                failureItemIndex: result.failureItemIndex,
                failureIndustryCodes: result.failureIndustryCodes,
                runId: result.reportRunId,
              }}
            />
          </details>
        )}
        {status.isError && (
          <p role="alert">任务状态读取失败，将继续查询；不会自动重跑模型。</p>
        )}
      </ManualRequestRecord>
    </section>
  );
}

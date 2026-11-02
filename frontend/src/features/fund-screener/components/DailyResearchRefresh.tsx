import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../../../api/daily-research-debug";
import { HttpError } from "../../../api/client";
import { timeLabel } from "../macro-display";
import { DailyResearchFailureDiagnosis } from "./DailyResearchReport";

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
}
export default function DailyResearchRefresh(props: Props) {
  return <RefreshControl key={props.actor} {...props} />;
}
function RefreshControl({ actor, onReady }: Props) {
  const client = useQueryClient();
  const [unconfirmedKey, setUnconfirmedKey] = useState(() =>
    readCommand(actor),
  );
  const commandKey = useRef<string | null>(unconfirmedKey);
  const completed = useRef<string | null>(null);
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
  return (
    <section className="op-debug-refresh" aria-label="手动研究">
      <button
        type="button"
        disabled={busy || submit.isPending}
        onClick={() =>
          submit.mutate(commandKey.current ?? crypto.randomUUID())
        }
      >
        {unconfirmedKey ? "重试本次提交" : "手动启动研究"}
      </button>
      <small>
        将重新采集最新公共信息并调用一次 Codex，产生新的用量；普通刷新不会重跑。
      </small>
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
      {expired && active(result?.status) ? (
        <p role="status">
          超过35分钟，执行结果待确认，正在继续查询后台结果。保留上一份有效报告，本次执行结束前不会启动新的模型。
        </p>
      ) : result?.status === "QUEUED" || result?.status === "RUNNING" ? (
        <p role="status">
          正在采集与分析 ·{" "}
          {result.status === "QUEUED" ? "等待后台处理" : "后台处理中"}
          。保留上一份有效报告。
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
      {(result?.status === "FAILED" || result?.status === "UNCERTAIN") && (
        <DailyResearchFailureDiagnosis
          data={{
            failureStage: result.failureStage,
            failureReasonCode: result.failureReasonCode,
            failureRuleId: result.failureRuleId,
            failureItemIndex: result.failureItemIndex,
            runId: result.reportRunId,
          }}
        />
      )}
      {status.isError && (
        <p role="alert">任务状态读取失败，将继续查询；不会自动重跑模型。</p>
      )}
      {result && <small>本次请求：{timeLabel(result.requestedAt)}</small>}
    </section>
  );
}

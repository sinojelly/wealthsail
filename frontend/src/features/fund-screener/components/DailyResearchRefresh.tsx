import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../../../api/daily-research-debug";
import { timeLabel } from "../macro-display";

const LIMIT = 35 * 60 * 1000;
const active = (status?: string) => status === "QUEUED" || status === "RUNNING";
interface Props {
  actor: string;
  onReady?: () => void;
}
export default function DailyResearchRefresh(props: Props) {
  return <RefreshControl key={props.actor} {...props} />;
}
function RefreshControl({ actor, onReady }: Props) {
  const client = useQueryClient();
  const commandKey = useRef<string | null>(null);
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
  const started = Date.parse(current?.requestedAt ?? "");
  const expired =
    !!current &&
    active(current.status) &&
    (!Number.isFinite(started) ||
      openedAt - started >= LIMIT ||
      timedOut === current.id);
  const status = useQuery({
    queryKey: [actor, "daily-research-debug-status", current?.id],
    queryFn: ({ signal }) => api.getResearchRefreshStatus(current!.id, signal),
    enabled:
      capability.data?.enabled === true &&
      !!current &&
      active(current.status) &&
      !expired,
    refetchInterval: (query) =>
      active(query.state.data?.status ?? current?.status) ? 3000 : false,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const result = status.data?.id === current?.id ? status.data : current;
  const busy = !!result && active(result.status) && !expired;
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
    mutationFn: () => {
      commandKey.current ??= crypto.randomUUID();
      return api.startResearchRefresh(commandKey.current);
    },
    retry: false,
    onSuccess: (value) => {
      setSubmitted(value);
      commandKey.current = null;
    },
  });
  if (capability.data?.enabled !== true) return null;
  const safeError =
    result?.errorClass &&
    /^[A-Za-z][A-Za-z0-9_.]{0,79}$/.test(result.errorClass)
      ? result.errorClass
      : "原因待核查";
  return (
    <section className="op-debug-refresh" aria-label="调试研究更新">
      <button
        type="button"
        disabled={busy || submit.isPending}
        onClick={() => submit.mutate()}
      >
        {submit.isError ? "重试本次提交" : "重新采集并分析"}
      </button>
      <small>调试功能：重新抓取新闻并运行一次模型；普通刷新不会重跑。</small>
      {submit.isError && (
        <p role="alert">
          提交结果待确认，可重试本次提交；相同请求不会重复运行。
        </p>
      )}
      {expired && active(result?.status) ? (
        <p role="status">
          超过35分钟，执行结果待确认。保留上一份有效报告，可检查后台后显式重新运行。
        </p>
      ) : result?.status === "QUEUED" || result?.status === "RUNNING" ? (
        <p role="status">
          正在采集与分析 ·{" "}
          {result.status === "QUEUED" ? "等待后台处理" : "后台处理中"}
          。保留上一份有效报告。
        </p>
      ) : result?.status === "READY" ? (
        <p role="status">调试研究已完成，页面将显示最新有效报告。</p>
      ) : result?.status === "FAILED" ? (
        <p role="alert">
          采集或分析失败：{safeError}。保留上一份有效报告，可重新运行。
        </p>
      ) : result?.status === "UNCERTAIN" ? (
        <p role="status">
          执行结果待确认：{safeError}。保留上一份有效报告，不自动重跑模型。
        </p>
      ) : null}
      {status.isError && (
        <p role="alert">任务状态读取失败，将继续查询；不会自动重跑模型。</p>
      )}
      {result && <small>本次请求：{timeLabel(result.requestedAt)}</small>}
    </section>
  );
}

import type { ResearchProgress } from "../../../api/research-progress";
import { formatShanghaiTime } from "../../operations/format-time";

const phases: Record<ResearchProgress["phase"], string> = {
  MODEL_RUNNING: "模型运行中",
  WAITING_TOOL: "等待工具返回",
  MODEL_MESSAGE: "正在接收模型消息",
  TURN_COMPLETED: "已收到模型完成事件",
  TURN_FAILED: "已收到执行失败事件",
};

export function ResearchProgressDetails({
  progress,
}: {
  progress?: ResearchProgress;
}) {
  if (progress?.version !== "research-progress-v1") return null;
  const count = (value?: number | null) =>
    value != null && Number.isInteger(value) && value >= 0 ? value : "未知";
  return (
    <section aria-label="研究运行进度">
      <p>执行观测：{phases[progress.phase] ?? "未知阶段"}</p>
      <p>
        调用观测开始：{formatShanghaiTime(progress.startedAt)} · 最后 CLI 事件：
        {formatShanghaiTime(progress.lastEventAt)}
      </p>
      <p>
        模型观察耗时：
        {Number.isFinite(progress.elapsedMs) && progress.elapsedMs >= 0
          ? `${(progress.elapsedMs / 1000).toFixed(2)}秒`
          : "未知"}{" "}
        · 已完成工具：{count(progress.completedToolActions)} · 活动工具：
        {count(progress.activeToolActions)}
      </p>
      <p>
        模型完成事件：
        {progress.modelCompleted === true
          ? "已收到"
          : progress.modelCompleted === false
            ? "未收到"
            : "未知"}
      </p>
      <p>
        实际搜索查询：{count(progress.observedSearchQueries)} · 页面打开：
        {count(progress.observedPageOpens)} · 页面查找：
        {count(progress.observedPageFinds)}
      </p>
      <p>这是最近一次只读观测，进程退出须由独立生命周期回执确认。</p>
    </section>
  );
}

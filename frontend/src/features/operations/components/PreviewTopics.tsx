import type { ResearchPreview } from "../../../api/research-preview";
import { sourceHref, timeLabel } from "../../fund-screener/macro-display";

export default function PreviewTopics({
  preview,
}: {
  preview: ResearchPreview;
}) {
  return (
    <section
      id={`topic-catalog-${preview.runId}`}
      aria-label="冻结研究主题与来源"
      className="research-preview-topics"
    >
      <h3>冻结研究主题目录</h3>
      {preview.topicRegistry?.items.length ? (
        preview.topicRegistry.items.map((topic) => (
          <article key={topic.topicId}>
            <h4>{topic.title}</h4>
            <p>{topic.summary}</p>
            <small>历史观察 · 不授予投资资格 · {topic.reason}</small>
            {topic.pendingTasks.length > 0 && (
              <details>
                <summary>原时点待补核查 {topic.pendingTasks.length} 项</summary>
                <ul>
                  {topic.pendingTasks.map((task) => (
                    <li key={task.taskId}>{task.query}</li>
                  ))}
                </ul>
              </details>
            )}
          </article>
        ))
      ) : (
        <p>原材料未保存主题目录。</p>
      )}
      <details>
        <summary>原研究引用来源</summary>
        {preview.report?.sources.map((source) => (
          <article key={source.id}>
            <strong>
              {sourceHref(source.url) ? (
                <a
                  href={sourceHref(source.url)}
                  target="_blank"
                  rel="noreferrer"
                >
                  {source.title}
                </a>
              ) : (
                source.title
              )}
            </strong>
            <p>{source.quote}</p>
            <small>
              {source.verification === "SOURCE_CHECKED"
                ? "原时点原文已核验"
                : "原时点来源未核验"}{" "}
              · {timeLabel(source.publishedAt ?? source.publishedDate)}
            </small>
          </article>
        ))}
      </details>
    </section>
  );
}

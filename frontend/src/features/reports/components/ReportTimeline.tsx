import { CheckCircle2, FileClock, Search, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import type { ReportSession, ReportSummary } from "../../../api/types";
import EmptyState from "../../../components/data-state/EmptyState";
import { formatShanghaiTime } from "../../../lib/shanghai-time";
import {
  REPORT_SESSION_META,
  REPORT_SESSION_ORDER,
  reportDisplayMeta,
} from "../report-data";

export default function ReportTimeline({
  reports,
  selectedId,
  onSelect,
  onGenerate,
}: {
  reports: ReportSummary[];
  selectedId: string;
  onSelect: (report: ReportSummary) => void;
  /** 触发尚未生成过任何报告的报告时段（无行可选中时的一键生成入口）。 */
  onGenerate: (session: ReportSession) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(
    () =>
      reports.filter((report) => {
        const display = reportDisplayMeta(report);
        return `${display.title}${display.subtitle}`.includes(query);
      }),
    [query, reports],
  );
  const missingSessions = useMemo(() => {
    const present = new Set(reports.map((report) => report.session));
    return REPORT_SESSION_ORDER.filter(
      (session) => !present.has(session),
    ) as ReportSession[];
  }, [reports]);
  const tradeDate = reports[0]?.tradeDate;
  const displayDate = tradeDate
    ? `${tradeDate.slice(0, 4)}年${Number(tradeDate.slice(5, 7))}月${Number(tradeDate.slice(8, 10))}日`
    : null;

  return (
    <aside className="report-timeline" aria-label="报告时间线">
      <div className="report-search">
        <Search size={14} />
        <input
          aria-label="搜索研报"
          value={query}
          placeholder="搜索报告内容"
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {displayDate ? (
        <div className="timeline-date">
          <span>{displayDate}</span>
          <i />
        </div>
      ) : null}
      <div
        className={`report-list ${
          filtered.length === 0 && missingSessions.length === 0 ? "is-empty" : ""
        }`}
      >
        {filtered.map((report) => {
          const generatedTime = formatShanghaiTime(report.generatedAt);
          const display = reportDisplayMeta(report);
          return (
            <button
              key={report.id}
              aria-label={`${display.title} ${generatedTime}`}
              className={`report-item ${report.id === selectedId ? "selected" : ""}`}
              onClick={() => onSelect(report)}
            >
              <div className="report-time mono">{generatedTime}</div>
              <div className="report-item-copy">
                <strong>{display.title}</strong>
                <span>{display.subtitle}</span>
                <small>
                  <CheckCircle2 size={10} />
                  {report.status} · v{report.version}
                </small>
              </div>
            </button>
          );
        })}
        {missingSessions.map((session) => {
          const meta = REPORT_SESSION_META[session];
          return (
            <button
              key={session}
              aria-label={`生成 ${meta.title}`}
              className="report-item report-item-placeholder"
              onClick={() => onGenerate(session)}
            >
              <div className="report-time mono" />
              <div className="report-item-copy">
                <strong>{meta.title}</strong>
                <span>{meta.subtitle}</span>
                <small>
                  <Sparkles size={10} />
                  尚未生成，点击生成并推送
                </small>
              </div>
            </button>
          );
        })}
        {filtered.length === 0 && missingSessions.length === 0 ? (
          <EmptyState
            className="report-list-empty"
            title={reports.length === 0 ? "暂无报告记录" : "未找到匹配报告"}
            detail={
              reports.length === 0
                ? "报告生成后会按交易日和版本显示在这里。"
                : "请尝试更换搜索关键词。"
            }
            icon={FileClock}
          />
        ) : null}
      </div>
      <div className="timeline-note">
        所有报告保留生成版本、事实快照与证据锚点；发生模型调用时同时保留模型运行标识。
      </div>
    </aside>
  );
}

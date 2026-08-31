import { CheckCircle2, FileClock, Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { ReportSummary } from "../../../api/types";
import EmptyState from "../../../components/data-state/EmptyState";
import { formatShanghaiTime } from "../../../lib/shanghai-time";

export default function ReportTimeline({
  reports,
  selectedId,
  onSelect,
}: {
  reports: ReportSummary[];
  selectedId: string;
  onSelect: (report: ReportSummary) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(
    () =>
      reports.filter((report) =>
        `${report.title}${report.subtitle}`.includes(query),
      ),
    [query, reports],
  );
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
      <div className={`report-list ${filtered.length === 0 ? "is-empty" : ""}`}>
        {filtered.map((report) => {
          const generatedTime = formatShanghaiTime(report.generatedAt);
          return (
            <button
              key={report.id}
              aria-label={`${report.title} ${generatedTime}`}
              className={`report-item ${report.id === selectedId ? "selected" : ""}`}
              onClick={() => onSelect(report)}
            >
              <div className="report-time mono">{generatedTime}</div>
              <div className="report-item-copy">
                <strong>{report.title}</strong>
                <span>{report.subtitle}</span>
                <small>
                  <CheckCircle2 size={10} />
                  {report.status} · v{report.version}
                </small>
              </div>
            </button>
          );
        })}
        {filtered.length === 0 ? (
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

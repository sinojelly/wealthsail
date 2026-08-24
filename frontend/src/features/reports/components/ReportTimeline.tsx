import { CheckCircle2, Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { ReportSummary } from "../../../api/types";

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
    : "—";

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
      <div className="timeline-date">
        <span>{displayDate}</span>
        <i />
      </div>
      <div className="report-list">
        {filtered.map((report) => (
          <button
            key={report.id}
            aria-label={`${report.title} ${report.generatedAt}`}
            className={`report-item ${report.id === selectedId ? "selected" : ""}`}
            onClick={() => onSelect(report)}
          >
            <div className="report-time mono">
              {report.generatedAt.slice(11, 16)}
            </div>
            <div className="report-item-copy">
              <strong>{report.title}</strong>
              <span>{report.subtitle}</span>
              <small>
                <CheckCircle2 size={10} />
                {report.status} · v{report.version}
              </small>
            </div>
          </button>
        ))}
        {filtered.length === 0 ? (
          <div className="report-list-empty">暂无报告</div>
        ) : null}
      </div>
      <div className="timeline-note">
        所有报告保留生成版本、事实快照、证据锚点与模型运行标识。
      </div>
    </aside>
  );
}

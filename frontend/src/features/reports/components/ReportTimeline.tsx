import { CheckCircle2, Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { ReportRecord } from "../report-data";

export default function ReportTimeline({
  reports,
  selectedId,
  onSelect,
}: {
  reports: ReportRecord[];
  selectedId: string;
  onSelect: (report: ReportRecord) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(
    () =>
      reports.filter((report) =>
        `${report.title}${report.subtitle}`.includes(query),
      ),
    [query, reports],
  );
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
        <span>2026年8月23日</span>
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
            <div className="report-time mono">{report.generatedAt}</div>
            <div className="report-item-copy">
              <strong>{report.title}</strong>
              <span>{report.subtitle}</span>
              <small>
                <CheckCircle2 size={10} />
                {report.status}
              </small>
            </div>
          </button>
        ))}
      </div>
      <div className="timeline-note">
        所有报告保留生成版本、事实快照、证据锚点与模型运行标识。
      </div>
    </aside>
  );
}

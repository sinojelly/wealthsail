import { useState } from "react";
import EvidenceDrawer from "./components/EvidenceDrawer";
import ReportTimeline from "./components/ReportTimeline";
import ReportViewer from "./components/ReportViewer";
import type { EvidenceRecord, ReportRecord } from "./report-data";
import { reports } from "./report-data";

export default function ReportsPage() {
  const [selected, setSelected] = useState<ReportRecord>(reports[0]);
  const [evidence, setEvidence] = useState<EvidenceRecord | null>(null);
  return (
    <div className="reports-workspace">
      <ReportTimeline
        reports={reports}
        selectedId={selected.id}
        onSelect={setSelected}
      />
      <ReportViewer report={selected} onEvidence={setEvidence} />
      <EvidenceDrawer evidence={evidence} onClose={() => setEvidence(null)} />
    </div>
  );
}

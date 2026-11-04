import type { DeterministicRepair } from "../../../api/investment";

export default function DeterministicRepairNotice({
  repair,
  reportRunId,
}: {
  repair?: DeterministicRepair;
  reportRunId?: string | null;
}) {
  if (
    !repair?.strictRevalidated ||
    repair.originalDecision !== "PARTIAL" ||
    !reportRunId
  )
    return null;
  return (
    <section aria-label="报告修正与隔离说明" className="op-research-issues">
      <strong className="op-badge">已隔离并严格重验</strong>
      <p>
        原始输出中{repair.quarantinedCount}
        项未通过，隔离后整份报告已严格重验并发布。
      </p>
      {!!repair.quarantined?.length && (
        <ul>
          {repair.quarantined.map((item, index) => (
            <li key={`${item.itemType}:${item.itemIndex}:${index}`}>
              {item.reason}
            </li>
          ))}
        </ul>
      )}
      <small>修正报告：{reportRunId}</small>
    </section>
  );
}

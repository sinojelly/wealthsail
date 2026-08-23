import type { QualityCode } from "../../api/generated/types";
import { qualityDescriptions, qualityLabels } from "../../domain/quality";

export default function QualityBadge({ quality }: { quality: QualityCode }) {
  return (
    <span
      className={`quality-badge quality-${quality.toLowerCase()}`}
      title={qualityDescriptions[quality]}
    >
      {qualityLabels[quality]}
    </span>
  );
}

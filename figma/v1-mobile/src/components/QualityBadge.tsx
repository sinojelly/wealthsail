import { getDataQualityLabel } from "../domain/reporting"
import type { DataQuality } from "../domain/types"

interface QualityBadgeProps {
  quality: DataQuality
}

export default function QualityBadge({ quality }: QualityBadgeProps) {
  const tone: Record<DataQuality, string> = {
    FRESH: "border-emerald-400/25 bg-emerald-400/10 text-emerald-300",
    ESTIMATED: "border-cyan-400/25 bg-cyan-400/10 text-cyan-300",
    STALE: "border-amber-400/25 bg-amber-400/10 text-amber-300",
    CONFLICT: "border-rose-400/25 bg-rose-400/10 text-rose-300",
    UNAVAILABLE: "border-slate-500/30 bg-slate-500/10 text-slate-400",
  }

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[11px] font-medium ${tone[quality]}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {getDataQualityLabel(quality)}
    </span>
  )
}

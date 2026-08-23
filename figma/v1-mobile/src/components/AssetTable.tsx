import type { Asset, Position } from "../domain/types"
import QualityBadge from "./QualityBadge"
import Sparkline from "./Sparkline"

interface AssetTableProps {
  assets: Asset[]
  positions?: Position[]
  onSelect: (asset: Asset) => void
  title?: string
}

const assetTypeLabel: Record<Asset["type"], string> = {
  CN_STOCK: "A 股",
  EXCHANGE_FUND: "场内基金",
  OFF_EXCHANGE_FUND: "场外基金",
}

export default function AssetTable({
  assets,
  positions = [],
  onSelect,
  title = "资产覆盖",
}: AssetTableProps) {
  return (
    <section className="border-t border-white/[0.07] pt-5">
      <div className="mb-3 flex items-end justify-between">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-500">
            Portfolio universe
          </p>
          <h2 className="mt-1 text-base font-semibold text-slate-100">
            {title}
          </h2>
        </div>
        <span className="text-xs text-slate-500">{assets.length} 项</span>
      </div>

      <div className="overflow-hidden border-y border-white/[0.07]">
        {assets.map((asset) => {
          const position = positions.find((item) => item.assetId === asset.id)
          return (
            <button
              className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-white/[0.06] px-1 py-3 text-left transition-colors last:border-b-0 hover:bg-white/[0.025] sm:grid-cols-[minmax(0,1.35fr)_110px_105px_auto]"
              key={asset.id}
              onClick={() => onSelect(asset)}
              type="button"
            >
              <span className="min-w-0">
                <span className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium text-slate-100">
                    {asset.name}
                  </span>
                  <span className="hidden text-[10px] text-slate-500 sm:inline">
                    {assetTypeLabel[asset.type]}
                  </span>
                </span>
                <span className="mt-1 block font-mono text-[11px] text-slate-500">
                  {asset.symbol} · {asset.market}
                </span>
              </span>
              <span className="hidden sm:block">
                <Sparkline
                  values={asset.sparkline}
                  positive={asset.changePercent >= 0}
                />
              </span>
              <span className="text-right">
                <span className="block font-mono text-sm text-slate-200">
                  {asset.price.toLocaleString("zh-CN", {
                    maximumFractionDigits: 4,
                  })}
                </span>
                <span
                  className={`font-mono text-xs ${
                    asset.changePercent >= 0
                      ? "text-emerald-400"
                      : "text-rose-400"
                  }`}
                >
                  {asset.changePercent >= 0 ? "+" : ""}
                  {asset.changePercent.toFixed(2)}%
                </span>
              </span>
              <span className="hidden min-w-24 justify-self-end sm:block">
                {position ? (
                  <span className="text-right text-[11px] text-slate-500">
                    <span className="block">持仓市值</span>
                    <span className="font-mono text-slate-300">
                      ¥
                      {position.marketValue.toLocaleString("zh-CN", {
                        maximumFractionDigits: 0,
                      })}
                    </span>
                  </span>
                ) : (
                  <QualityBadge quality={asset.quality} />
                )}
              </span>
            </button>
          )
        })}
      </div>
    </section>
  )
}

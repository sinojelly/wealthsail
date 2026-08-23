import type { Asset } from "../domain/types"
import QualityBadge from "./QualityBadge"
import Sparkline from "./Sparkline"

interface ContextPanelProps {
  asset: Asset
  onClose: () => void
}

const typeLabel: Record<Asset["type"], string> = {
  CN_STOCK: "A 股",
  EXCHANGE_FUND: "场内基金",
  OFF_EXCHANGE_FUND: "场外基金",
}

export default function ContextPanel({ asset, onClose }: ContextPanelProps) {
  return (
    <aside className="absolute inset-y-0 right-0 z-40 w-full border-l border-white/[0.08] bg-[#0a1426] p-6 shadow-2xl shadow-black/50 sm:w-[340px]">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[10px] uppercase tracking-[0.18em] text-slate-600">
            {typeLabel[asset.type]} · {asset.symbol}
          </p>
          <h2 className="mt-2 text-xl font-semibold text-white">
            {asset.name}
          </h2>
        </div>
        <button
          aria-label="关闭资产详情"
          className="grid h-8 w-8 place-items-center text-slate-500 transition-colors hover:bg-white/[0.05] hover:text-slate-200"
          onClick={onClose}
          type="button"
        >
          ×
        </button>
      </div>

      <div className="mt-7 flex items-end justify-between border-y border-white/[0.07] py-5">
        <div>
          <p className="font-mono text-3xl text-slate-100">
            {asset.price.toLocaleString("zh-CN", { maximumFractionDigits: 4 })}
          </p>
          <p
            className={`mt-2 font-mono text-sm ${
              asset.changePercent >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {asset.changePercent >= 0 ? "+" : ""}
            {asset.changePercent.toFixed(2)}%
          </p>
        </div>
        <Sparkline
          values={asset.sparkline}
          positive={asset.changePercent >= 0}
        />
      </div>

      <dl className="mt-6 space-y-4 text-xs">
        <div className="flex items-center justify-between">
          <dt className="text-slate-600">数据状态</dt>
          <dd>
            <QualityBadge quality={asset.quality} />
          </dd>
        </div>
        <div className="flex items-center justify-between">
          <dt className="text-slate-600">价格时间</dt>
          <dd className="font-mono text-slate-400">
            {asset.priceAsOf.slice(0, 16).replace("T", " ")}
          </dd>
        </div>
        <div className="flex items-start justify-between gap-4">
          <dt className="text-slate-600">标签</dt>
          <dd className="flex flex-wrap justify-end gap-2">
            {asset.tags.map((tag) => (
              <span
                className="border border-white/[0.08] px-2 py-1 text-[10px] text-slate-400"
                key={tag}
              >
                {tag}
              </span>
            ))}
          </dd>
        </div>
      </dl>

      <div className="mt-7 border-l-2 border-amber-400 bg-amber-400/[0.04] p-4">
        <p className="text-xs font-medium text-amber-300">研究边界</p>
        <p className="mt-2 text-xs leading-5 text-slate-500">
          当前仅展示演示行情和证据状态。任何买卖、申赎或通知动作都需要服务端鉴权与用户确认。
        </p>
      </div>
    </aside>
  )
}

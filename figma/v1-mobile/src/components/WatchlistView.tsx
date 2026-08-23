import type { Asset, Tenant } from "../domain/types"
import AssetTable from "./AssetTable"

interface WatchlistViewProps {
  tenant: Tenant
  assets: Asset[]
  onAssetSelect: (asset: Asset) => void
}

export default function WatchlistView({
  tenant,
  assets,
  onAssetSelect,
}: WatchlistViewProps) {
  return (
    <div className="mx-auto w-full max-w-6xl px-4 pb-24 pt-7 sm:px-7 sm:pb-10">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-emerald-400">
        Watch & verify
      </p>
      <h1 className="mt-3 text-3xl font-semibold text-white">观察清单</h1>
      <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-500">
        {tenant.name} ·
        观察项不等同于持仓。系统只有在事实或预设触发条件发生变化时才提醒。
      </p>

      <div className="mt-7">
        <AssetTable
          assets={assets}
          onSelect={onAssetSelect}
          title="跨品类观察资产"
        />
      </div>
    </div>
  )
}

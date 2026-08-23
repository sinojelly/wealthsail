import type { Asset, Position, Tenant } from "../domain/types"
import AssetTable from "./AssetTable"

interface PortfolioViewProps {
  tenant: Tenant
  assets: Asset[]
  positions: Position[]
  onAssetSelect: (asset: Asset) => void
}

export default function PortfolioView({
  tenant,
  assets,
  positions,
  onAssetSelect,
}: PortfolioViewProps) {
  const marketValue = positions.reduce(
    (sum, position) => sum + position.marketValue,
    0,
  )
  const investedCost = positions.reduce(
    (sum, position) => sum + position.costBasis * position.quantity,
    0,
  )
  const pnl = marketValue - investedCost
  const positionAssets = assets.filter((asset) =>
    positions.some((position) => position.assetId === asset.id),
  )

  return (
    <div className="mx-auto w-full max-w-6xl px-4 pb-24 pt-7 sm:px-7 sm:pb-10">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-emerald-400">
        Portfolio ledger
      </p>
      <h1 className="mt-3 text-3xl font-semibold text-white">组合持仓</h1>
      <p className="mt-3 text-sm text-slate-500">
        {tenant.name} · 当前仅展示隔离后的账户数据
      </p>

      <section className="mt-7 grid gap-px bg-white/[0.07] sm:grid-cols-3">
        <div className="bg-[#091221] p-5">
          <p className="text-[10px] uppercase tracking-[0.16em] text-slate-600">
            总市值
          </p>
          <p className="mt-2 font-mono text-2xl text-slate-100">
            ¥{marketValue.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}
          </p>
        </div>
        <div className="bg-[#091221] p-5">
          <p className="text-[10px] uppercase tracking-[0.16em] text-slate-600">
            累计盈亏
          </p>
          <p
            className={`mt-2 font-mono text-2xl ${
              pnl >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {pnl >= 0 ? "+" : ""}¥
            {pnl.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}
          </p>
        </div>
        <div className="bg-[#091221] p-5">
          <p className="text-[10px] uppercase tracking-[0.16em] text-slate-600">
            资产类型
          </p>
          <p className="mt-2 font-mono text-2xl text-slate-100">
            {new Set(positionAssets.map((asset) => asset.type)).size}
          </p>
        </div>
      </section>

      <div className="mt-7">
        <AssetTable
          assets={positionAssets}
          onSelect={onAssetSelect}
          positions={positions}
          title="真实持仓账本"
        />
      </div>
    </div>
  )
}

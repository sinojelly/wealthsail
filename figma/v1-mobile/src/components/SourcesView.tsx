import type { SourceEvidence } from "../domain/types"
import QualityBadge from "./QualityBadge"

interface SourcesViewProps {
  evidence: SourceEvidence[]
}

const sourceTypeLabel: Record<SourceEvidence["sourceType"], string> = {
  MARKET_DATA: "市场数据",
  DISCLOSURE: "权威披露",
  RESEARCH: "研究材料",
  MEDIA: "媒体候选源",
}

export default function SourcesView({ evidence }: SourcesViewProps) {
  return (
    <div className="mx-auto w-full max-w-6xl px-4 pb-24 pt-7 sm:px-7 sm:pb-10">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-emerald-400">
        Evidence registry
      </p>
      <h1 className="mt-3 text-3xl font-semibold text-white">来源中心</h1>
      <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-500">
        先保存来源、发布时间、抓取时间与授权状态，再让模型摘要。兔主席、苏涵等内容当前仅作为待授权候选源，不复制原文，不直接生成交易指令。
      </p>

      <div className="mt-7 border-y border-white/[0.07]">
        {evidence.map((source) => (
          <a
            className="grid gap-3 border-b border-white/[0.06] py-4 transition-colors last:border-b-0 hover:bg-white/[0.02] sm:grid-cols-[120px_minmax(0,1fr)_auto] sm:items-center sm:px-3"
            href={source.url}
            key={source.id}
            rel="noreferrer"
            target="_blank"
          >
            <span className="text-[10px] uppercase tracking-[0.16em] text-slate-600">
              {sourceTypeLabel[source.sourceType]}
            </span>
            <span>
              <span className="block text-sm font-medium text-slate-200">
                {source.publisher}
              </span>
              <span className="mt-1 block text-xs text-slate-500">
                {source.title}
              </span>
            </span>
            <span className="flex items-center gap-3">
              <QualityBadge quality={source.quality} />
              <span className="text-slate-600">↗</span>
            </span>
          </a>
        ))}
      </div>

      <section className="mt-7 grid gap-px bg-white/[0.07] sm:grid-cols-3">
        {[
          [
            "01",
            "抓取与授权",
            "优先使用公开接口或获得授权的内容，记录许可范围。",
          ],
          [
            "02",
            "转写与切片",
            "保留原始时间码、作者和链接，失败可重试且不重复入库。",
          ],
          [
            "03",
            "观点与事实分离",
            "模型输出标注主张、证据和反例，媒体观点不提升为事实。",
          ],
        ].map(([step, title, body]) => (
          <article className="bg-[#091221] p-5" key={step}>
            <span className="font-mono text-[10px] text-emerald-400">
              {step}
            </span>
            <h2 className="mt-3 text-sm font-semibold text-slate-200">
              {title}
            </h2>
            <p className="mt-2 text-xs leading-5 text-slate-500">{body}</p>
          </article>
        ))}
      </section>
    </div>
  )
}

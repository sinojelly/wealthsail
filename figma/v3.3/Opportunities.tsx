import { useMemo, useState } from "react"
import {
  Activity,
  ArrowRight,
  Check,
  ChevronDown,
  Droplets,
  Gauge,
  Globe2,
  Info,
  Landmark,
  LineChart,
  ListPlus,
  Search,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
  Star,
  X,
} from "lucide-react"

type MarketId = "a-share" | "hk" | "us" | "bond" | "gold" | "other"
type StyleId = "broad" | "dividend" | "growth" | "value" | "technology" | "consumer" | "cyclical" | "low-vol" | "government" | "credit" | "physical"
type DiagnosisTab = "decision" | "trend" | "quality"

type Fund = {
  code: string
  name: string
  type: "ETF" | "场外指数基金" | "主动基金"
}

type IndexCandidate = {
  id: string
  code: string
  name: string
  exposure: string
  valuationMetric: string
  funds: Fund[]
}

type ResearchProfile = {
  title: string
  framework: string
  supportFactors: string[]
  opposingFactors: string[]
  indices: IndexCandidate[]
}

const markets: Array<{ id: MarketId label: string enabled: boolean }> = [
  { id: "a-share", label: "A股", enabled: true },
  { id: "hk", label: "港股", enabled: true },
  { id: "us", label: "美股", enabled: false },
  { id: "bond", label: "债券", enabled: true },
  { id: "gold", label: "黄金", enabled: true },
  { id: "other", label: "其他", enabled: false },
]

const stylesByMarket: Record<MarketId, Array<{ id: StyleId label: string }>> = {
  "a-share": [
    { id: "broad", label: "宽基" },
    { id: "dividend", label: "红利" },
    { id: "growth", label: "成长" },
    { id: "value", label: "价值" },
    { id: "technology", label: "科技" },
    { id: "consumer", label: "消费" },
    { id: "cyclical", label: "周期" },
    { id: "low-vol", label: "低波" },
  ],
  hk: [
    { id: "broad", label: "宽基" },
    { id: "dividend", label: "红利" },
    { id: "technology", label: "科技" },
  ],
  us: [
    { id: "broad", label: "宽基" },
    { id: "technology", label: "科技" },
  ],
  bond: [
    { id: "government", label: "利率债" },
    { id: "credit", label: "信用债" },
  ],
  gold: [{ id: "physical", label: "黄金现货" }],
  other: [{ id: "broad", label: "全部" }],
}

const profiles: Record<string, ResearchProfile> = {
  "a-share:broad": {
    title: "A股 · 宽基",
    framework: "研究中国核心权益资产的盈利周期、估值水平、流动性与风险偏好。",
    supportFactors: ["经济增长", "货币流动性", "企业盈利"],
    opposingFactors: ["市场风险", "外部冲击"],
    indices: [
      {
        id: "hs300",
        code: "000300",
        name: "沪深300",
        exposure: "大盘核心资产",
        valuationMetric: "PE / PB 分位",
        funds: [
          { code: "510300", name: "华泰柏瑞沪深300ETF", type: "ETF" },
          { code: "510330", name: "华夏沪深300ETF", type: "ETF" },
          {
            code: "110020",
            name: "易方达沪深300ETF联接A",
            type: "场外指数基金",
          },
        ],
      },
      {
        id: "a500",
        code: "000510",
        name: "中证A500",
        exposure: "行业均衡核心资产",
        valuationMetric: "PE / PB 分位",
        funds: [
          { code: "512050", name: "华夏中证A500ETF", type: "ETF" },
          { code: "159338", name: "国泰中证A500ETF", type: "ETF" },
          { code: "022448", name: "摩根中证A500指数增强A", type: "主动基金" },
        ],
      },
      {
        id: "zz500",
        code: "000905",
        name: "中证500",
        exposure: "中盘成长",
        valuationMetric: "PE / PB 分位",
        funds: [
          { code: "510500", name: "南方中证500ETF", type: "ETF" },
          { code: "510580", name: "易方达中证500ETF", type: "ETF" },
        ],
      },
    ],
  },
  "a-share:dividend": {
    title: "A股 · 红利",
    framework: "研究股息可持续性、现金流质量、利率环境以及行业集中风险。",
    supportFactors: ["货币流动性", "现金流质量", "股息率"],
    opposingFactors: ["利率上行", "行业集中"],
    indices: [
      {
        id: "csdiv",
        code: "000922",
        name: "中证红利",
        exposure: "传统高股息",
        valuationMetric: "股息率 / PE 分位",
        funds: [
          { code: "515180", name: "易方达中证红利ETF", type: "ETF" },
          { code: "090010", name: "大成中证红利指数A", type: "场外指数基金" },
        ],
      },
      {
        id: "divlow",
        code: "H30269",
        name: "红利低波",
        exposure: "高股息与低波动",
        valuationMetric: "股息率 / 波动分位",
        funds: [{ code: "512890", name: "华泰柏瑞红利低波ETF", type: "ETF" }],
      },
    ],
  },
  "a-share:growth": {
    title: "A股 · 成长",
    framework: "研究盈利增速、产业趋势、估值消化能力和长久期资产的利率敏感性。",
    supportFactors: ["盈利增速", "产业景气", "风险偏好"],
    opposingFactors: ["估值压力", "波动风险"],
    indices: [
      {
        id: "chinext",
        code: "399006",
        name: "创业板指",
        exposure: "创新成长企业",
        valuationMetric: "PE / 盈利增速",
        funds: [
          { code: "159915", name: "易方达创业板ETF", type: "ETF" },
          {
            code: "110026",
            name: "易方达创业板ETF联接A",
            type: "场外指数基金",
          },
        ],
      },
      {
        id: "star50",
        code: "000688",
        name: "科创50",
        exposure: "硬科技龙头",
        valuationMetric: "PE / PS 分位",
        funds: [{ code: "588000", name: "华夏上证科创板50ETF", type: "ETF" }],
      },
    ],
  },
  "hk:broad": {
    title: "港股 · 宽基",
    framework: "研究港股盈利、南向资金、美元流动性和风险溢价。",
    supportFactors: ["盈利预期", "南向资金", "风险溢价"],
    opposingFactors: ["美元利率", "汇率风险"],
    indices: [
      {
        id: "hsi",
        code: "HSI",
        name: "恒生指数",
        exposure: "港股大盘",
        valuationMetric: "PE / PB 分位",
        funds: [{ code: "159920", name: "华夏恒生ETF", type: "ETF" }],
      },
      {
        id: "hscei",
        code: "HSCEI",
        name: "恒生中国企业指数",
        exposure: "在港上市中国企业",
        valuationMetric: "PE / PB 分位",
        funds: [{ code: "510900", name: "易方达恒生国企ETF", type: "ETF" }],
      },
    ],
  },
  "hk:dividend": {
    title: "港股 · 红利",
    framework: "研究港股高股息资产的分红持续性、汇率影响与行业集中度。",
    supportFactors: ["股息率", "南向资金", "现金流"],
    opposingFactors: ["汇率风险", "行业集中"],
    indices: [
      {
        id: "hkhyd",
        code: "930914",
        name: "港股通高股息",
        exposure: "港股通高股息资产",
        valuationMetric: "股息率 / PE 分位",
        funds: [{ code: "513820", name: "汇添富港股通高股息ETF", type: "ETF" }],
      },
    ],
  },
  "bond:government": {
    title: "债券 · 利率债",
    framework: "研究经济增长、通胀、政策利率与收益率曲线变化。",
    supportFactors: ["经济增长", "通胀环境", "货币流动性"],
    opposingFactors: ["利率上行", "久期风险"],
    indices: [
      {
        id: "cgb10",
        code: "CBA04501",
        name: "中债-10年期国债指数",
        exposure: "中长久期利率债",
        valuationMetric: "到期收益率 / 历史分位",
        funds: [{ code: "511260", name: "国泰上证10年期国债ETF", type: "ETF" }],
      },
    ],
  },
  "gold:physical": {
    title: "黄金 · 现货",
    framework: "研究实际利率、美元、央行购金与避险需求，不使用股票估值口径。",
    supportFactors: ["实际利率", "避险需求", "央行购金"],
    opposingFactors: ["美元走强", "持有成本"],
    indices: [
      {
        id: "au9999",
        code: "AU9999",
        name: "上海金 AU9999",
        exposure: "人民币计价黄金",
        valuationMetric: "实际利率 / 价格分位",
        funds: [
          { code: "518880", name: "华安黄金ETF", type: "ETF" },
          { code: "000216", name: "华安黄金ETF联接A", type: "场外指数基金" },
        ],
      },
    ],
  },
}

const macroDimensions = [
  {
    id: "growth",
    label: "经济增长",
    icon: Activity,
    indicators: "PMI · 工业生产",
    impact: "关联：宽基、周期、信用债",
  },
  {
    id: "inflation",
    label: "通胀环境",
    icon: Gauge,
    indicators: "CPI · PPI",
    impact: "关联：债券、黄金、消费",
  },
  {
    id: "liquidity",
    label: "货币流动性",
    icon: Droplets,
    indicators: "利率 · 信用",
    impact: "关联：成长、红利、债券",
  },
  {
    id: "risk",
    label: "市场风险",
    icon: ShieldAlert,
    indicators: "波动 · 风险溢价",
    impact: "关联：全部风险资产",
  },
] as const

function unavailableLabel(label = "尚未评估") {
  return (
    <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600">
      {label}
    </span>
  )
}

export default function Opportunities() {
  const [marketId, setMarketId] = useState<MarketId>("a-share")
  const [styleId, setStyleId] = useState<StyleId>("broad")
  const [styleMemory, setStyleMemory] =
    useState<Partial<Record<MarketId, StyleId>>>({ "a-share": "broad" })
  const profile = profiles[`${marketId}:${styleId}`] ?? {
    title: `${markets.find((item) => item.id === marketId)?.label} · ${stylesByMarket[
      marketId
    ].find((item) => item.id === styleId)?.label}`,
    framework: "该研究方向的数据映射与指数覆盖正在建设中。",
    supportFactors: [],
    opposingFactors: [],
    indices: [],
  }

  const [indexId, setIndexId] = useState("hs300")
  const selectedIndex =
    profile.indices.find((item) => item.id === indexId) ?? profile.indices[0]
  const [fundCode, setFundCode] = useState("510300")
  const selectedFund =
    selectedIndex?.funds.find((fund) => fund.code === fundCode) ??
    selectedIndex?.funds[0]

  const [objectMode, setObjectMode] = useState<"index" | "fund">("index")
  const [macroOpen, setMacroOpen] = useState(false)
  const [researchOpen, setResearchOpen] = useState(false)
  const [moreFundColumns, setMoreFundColumns] = useState(false)
  const [signalDetailsOpen, setSignalDetailsOpen] = useState(false)
  const [diagnosisTab, setDiagnosisTab] = useState<DiagnosisTab>("decision")
  const [query, setQuery] = useState("")
  const [searchFocused, setSearchFocused] = useState(false)
  const [watching, setWatching] = useState(false)

  const searchableItems = useMemo(
    () =>
      Object.entries(profiles).flatMap(([profileKey, item]) => {
        const [market, style] = profileKey.split(":") as [MarketId, StyleId]
        return item.indices.flatMap((index) => [
          {
            key: `index-${index.id}`,
            kind: "指数",
            title: index.name,
            code: index.code,
            market,
            style,
            indexId: index.id,
            fundCode: index.funds[0]?.code,
          },
          ...index.funds.map((fund) => ({
            key: `fund-${index.id}-${fund.code}`,
            kind: fund.type,
            title: fund.name,
            code: fund.code,
            market,
            style,
            indexId: index.id,
            fundCode: fund.code,
          })),
        ])
      }),
    [],
  )

  const searchResults = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    if (!normalized) return []
    return searchableItems
      .filter(
        (item) =>
          item.title.toLowerCase().includes(normalized) ||
          item.code.toLowerCase().includes(normalized),
      )
      .slice(0, 7)
  }, [query, searchableItems])

  const chooseMarket = (nextMarket: MarketId) => {
    const rememberedStyle = styleMemory[nextMarket]
    const nextStyle =
      stylesByMarket[nextMarket].find((style) => style.id === rememberedStyle)
        ?.id ?? stylesByMarket[nextMarket][0].id
    const nextProfile = profiles[`${nextMarket}:${nextStyle}`]
    setMarketId(nextMarket)
    setStyleId(nextStyle)
    setIndexId(nextProfile?.indices[0]?.id ?? "")
    setFundCode(nextProfile?.indices[0]?.funds[0]?.code ?? "")
    setObjectMode("index")
    setDiagnosisTab("decision")
    setResearchOpen(false)
    setWatching(false)
  }

  const chooseStyle = (nextStyle: StyleId) => {
    const nextProfile = profiles[`${marketId}:${nextStyle}`]
    setStyleId(nextStyle)
    setStyleMemory((memory) => ({ ...memory, [marketId]: nextStyle }))
    setIndexId(nextProfile?.indices[0]?.id ?? "")
    setFundCode(nextProfile?.indices[0]?.funds[0]?.code ?? "")
    setObjectMode("index")
    setDiagnosisTab("decision")
    setResearchOpen(false)
    setWatching(false)
  }

  const chooseIndex = (nextIndex: IndexCandidate) => {
    setIndexId(nextIndex.id)
    setFundCode(nextIndex.funds[0]?.code ?? "")
    setObjectMode("index")
    setDiagnosisTab("decision")
    setWatching(false)
  }

  const chooseFund = (fund: Fund) => {
    setFundCode(fund.code)
    setObjectMode("fund")
    setDiagnosisTab("decision")
    setWatching(false)
  }

  const selectSearchResult = (result: typeof searchResults[number]) => {
    setMarketId(result.market)
    setStyleId(result.style)
    setStyleMemory((memory) => ({
      ...memory,
      [result.market]: result.style,
    }))
    setIndexId(result.indexId)
    setFundCode(result.fundCode ?? "")
    setObjectMode(result.kind === "指数" ? "index" : "fund")
    setDiagnosisTab("decision")
    setQuery("")
    setSearchFocused(false)
    setWatching(false)
  }

  const marketLabel = markets.find((item) => item.id === marketId)?.label
  const styleLabel = stylesByMarket[marketId].find(
    (item) => item.id === styleId,
  )?.label
  const selectedTitle =
    objectMode === "fund"
      ? (selectedFund?.name ?? "请选择基金")
      : (selectedIndex?.name ?? "请选择指数")
  const selectedCode =
    objectMode === "fund" ? selectedFund?.code : selectedIndex?.code

  return (
    <div className="mx-auto max-w-screen-2xl space-y-4 p-5">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-primary" />
            <h1 className="text-xl font-semibold tracking-tight text-foreground">
              投资机会中心
            </h1>
            <span className="rounded bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700">
              研究模式
            </span>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            从市场环境到指数与基金的连续研究工作台
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="h-2 w-2 rounded-full bg-amber-400" />
          研究数据未接入 · 不生成行情、评分与买卖结论
        </div>
      </header>

      <div className="relative">
        <Search
          size={17}
          className="absolute left-4 top-1/2 z-10 -translate-y-1/2 text-muted-foreground"
        />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onFocus={() => setSearchFocused(true)}
          onBlur={() => setSearchFocused(false)}
          placeholder="全局搜索指数、ETF、场外基金或代码"
          className="h-11 w-full rounded-xl border border-border bg-white pl-11 pr-12 text-sm text-foreground shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/10"
        />
        {query && (
          <button
            onClick={() => setQuery("")}
            className="absolute right-4 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:bg-secondary"
            title="清除搜索"
          >
            <X size={14} />
          </button>
        )}
        {searchFocused && query && (
          <div className="absolute inset-x-0 top-12 z-30 overflow-hidden rounded-xl border border-border bg-white shadow-xl">
            {searchResults.length > 0 ? (
              <div className="divide-y divide-border">
                {searchResults.map((result) => (
                  <button
                    key={result.key}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => selectSearchResult(result)}
                    className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-card"
                  >
                    <div className="flex items-center gap-3">
                      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-secondary text-muted-foreground">
                        {result.kind === "指数" ? (
                          <LineChart size={14} />
                        ) : (
                          <Landmark size={14} />
                        )}
                      </span>
                      <div>
                        <div className="text-sm font-medium text-foreground">
                          {result.title}
                        </div>
                        <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                          {result.code}
                        </div>
                      </div>
                    </div>
                    <span className="rounded-full bg-secondary px-2 py-1 text-xs text-secondary-foreground">
                      {result.kind}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="px-4 py-6 text-center text-sm text-muted-foreground">
                暂无匹配的研究标的
              </div>
            )}
          </div>
        )}
      </div>

      <section className="overflow-hidden rounded-xl border border-border bg-white">
        <button
          onClick={() => setMacroOpen((open) => !open)}
          className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left"
        >
          <div className="flex min-w-0 items-center gap-3">
            <Globe2 size={15} className="shrink-0 text-primary" />
            <span className="shrink-0 text-sm font-semibold text-foreground">
              市场环境概览
            </span>
            <span className="truncate text-xs text-muted-foreground">
              增长 · 通胀 · 流动性 · 风险
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            {unavailableLabel("数据待接入")}
            <ChevronDown
              size={14}
              className={`text-muted-foreground transition-transform ${
                macroOpen ? "rotate-180" : ""
              }`}
            />
          </div>
        </button>
        {macroOpen && (
          <div className="grid border-t border-border sm:grid-cols-2 xl:grid-cols-4">
            {macroDimensions.map((item) => {
              const Icon = item.icon
              return (
                <div
                  key={item.id}
                  className="border-b border-border p-3 last:border-b-0 sm:border-r xl:border-b-0"
                >
                  <div className="flex items-center gap-2">
                    <Icon size={14} className="text-muted-foreground" />
                    <span className="text-xs font-medium text-foreground">
                      {item.label}
                    </span>
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground">
                    {item.indicators}
                  </div>
                  <div className="mt-1 text-xs text-slate-400">
                    {item.impact}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </section>

      <section className="overflow-hidden rounded-xl border border-border bg-white">
        <div className="flex flex-wrap items-center gap-x-7 gap-y-3 px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">
              市场
            </span>
            <div className="flex flex-wrap gap-1">
              {markets.map((market) => (
                <button
                  key={market.id}
                  disabled={!market.enabled}
                  onClick={() => chooseMarket(market.id)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                    marketId === market.id
                      ? "bg-slate-900 text-white"
                      : market.enabled
                        ? "text-secondary-foreground hover:bg-secondary"
                        : "cursor-not-allowed text-slate-300"
                  }`}
                  title={market.enabled ? undefined : "研究覆盖正在建设"}
                >
                  {market.label}
                </button>
              ))}
            </div>
          </div>
          <div className="hidden h-6 w-px bg-border lg:block" />
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">
              方向
            </span>
            <div className="flex flex-wrap gap-1">
              {stylesByMarket[marketId].map((style) => (
                <button
                  key={style.id}
                  onClick={() => chooseStyle(style.id)}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                    styleId === style.id
                      ? "bg-primary text-white"
                      : "text-secondary-foreground hover:bg-secondary"
                  }`}
                >
                  {style.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border bg-card px-4 py-2.5">
          <div className="min-w-0 text-xs text-secondary-foreground">
            <span className="font-semibold text-foreground">
              当前研究：{profile.title}
            </span>
            <span className="ml-2 text-muted-foreground">
              {profile.framework}
            </span>
          </div>
          <button
            onClick={() => setResearchOpen((open) => !open)}
            className="flex shrink-0 items-center gap-1 text-xs font-medium text-primary"
          >
            {researchOpen ? "收起依据" : "查看驱动与风险"}
            <ChevronDown
              size={12}
              className={`transition-transform ${
                researchOpen ? "rotate-180" : ""
              }`}
            />
          </button>
        </div>
        {researchOpen && (
          <div className="grid gap-4 border-t border-border px-4 py-3 md:grid-cols-[1fr_1fr_auto]">
            <div>
              <div className="text-xs font-medium text-foreground">
                待验证的支持因素
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                引用市场环境中的同源指标
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {profile.supportFactors.length > 0
                  ? profile.supportFactors.map((factor) => (
                      <span
                        key={factor}
                        className="rounded-full border border-border bg-white px-2 py-1 text-xs text-muted-foreground"
                      >
                        {factor}
                      </span>
                    ))
                  : unavailableLabel()}
              </div>
            </div>
            <div>
              <div className="text-xs font-medium text-foreground">
                待验证的反方因素
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {profile.opposingFactors.length > 0
                  ? profile.opposingFactors.map((factor) => (
                      <span
                        key={factor}
                        className="rounded-full border border-border bg-white px-2 py-1 text-xs text-muted-foreground"
                      >
                        {factor}
                      </span>
                    ))
                  : unavailableLabel()}
              </div>
            </div>
            <div className="rounded-lg border border-border bg-white px-3 py-2">
              <div className="text-xs text-muted-foreground">数据可靠度</div>
              <div className="mt-1 font-mono text-sm font-semibold text-foreground">
                0 / 4
              </div>
            </div>
          </div>
        )}
      </section>

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(390px,0.85fr)]">
        <section className="overflow-hidden rounded-xl border border-border bg-white">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground">
                候选选择
              </h2>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                <span>{marketLabel}</span>
                <span>/</span>
                <span>{styleLabel}</span>
                {selectedIndex && (
                  <>
                    <span>/</span>
                    <span className="font-medium text-foreground">
                      {selectedIndex.name}
                    </span>
                  </>
                )}
                {objectMode === "fund" && selectedFund && (
                  <>
                    <span>/</span>
                    <span className="font-mono font-medium text-primary">
                      {selectedFund.code}
                    </span>
                  </>
                )}
              </div>
            </div>
            <span className="text-xs text-muted-foreground">
              选择指数研究风险暴露，选择基金研究投资工具
            </span>
          </div>

          <div className="border-b border-border">
            <div className="flex items-center justify-between bg-card px-4 py-2.5">
              <span className="text-xs font-semibold text-foreground">
                指数选择
              </span>
              <span className="text-xs text-muted-foreground">
                {profile.indices.length} 个候选 · 趋势、估值与风险数据接入后启用
              </span>
            </div>
            {profile.indices.length > 0 ? (
              <div className="divide-y divide-border">
                {profile.indices.map((index) => {
                  const active = selectedIndex?.id === index.id
                  return (
                    <button
                      key={index.id}
                      onClick={() => chooseIndex(index)}
                      className={`grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-3 text-left transition-colors ${
                        active ? "bg-teal-50/60" : "hover:bg-card"
                      }`}
                    >
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-sm font-medium text-foreground">
                            {index.name}
                          </span>
                          {active && (
                            <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary text-white">
                              <Check size={10} />
                            </span>
                          )}
                        </div>
                        <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                          {index.code} · {index.exposure}
                        </div>
                      </div>
                      <div className="flex items-center gap-1 text-xs font-medium text-primary">
                        {active ? "当前指数" : "查看"}
                        {!active && <ArrowRight size={12} />}
                      </div>
                    </button>
                  )
                })}
              </div>
            ) : (
              <div className="px-4 py-9 text-center">
                <SlidersHorizontal
                  size={22}
                  className="mx-auto text-slate-300"
                />
                <div className="mt-2 text-sm font-medium text-foreground">
                  该方向尚未建立指数覆盖
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  请切换方向或使用全局搜索。
                </div>
              </div>
            )}
          </div>

          {selectedIndex && (
            <div>
              <div className="flex items-center justify-between bg-card px-4 py-2.5">
                <div>
                  <span className="text-xs font-semibold text-foreground">
                    跟踪基金
                  </span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    {selectedIndex.name}
                  </span>
                </div>
                <button
                  onClick={() => setMoreFundColumns((open) => !open)}
                  className="hidden items-center gap-1 text-xs font-medium text-primary md:flex"
                >
                  {moreFundColumns ? "收起更多列" : "展开更多列"}
                  <ChevronDown
                    size={12}
                    className={`transition-transform ${
                      moreFundColumns ? "rotate-180" : ""
                    }`}
                  />
                </button>
              </div>
              <div className="hidden overflow-x-auto md:block">
                <table
                  className={`w-full table-fixed text-left ${
                    moreFundColumns ? "min-w-[820px]" : ""
                  }`}
                >
                  <thead className="border-b border-border text-xs text-muted-foreground">
                    <tr>
                      <th className="w-2/5 px-4 py-2.5 font-medium">
                        基金名称 / 代码
                      </th>
                      <th className="px-3 py-2.5 font-medium">类型</th>
                      <th className="px-3 py-2.5 font-medium">成本</th>
                      <th className="px-3 py-2.5 font-medium">跟踪质量</th>
                      {moreFundColumns && (
                        <>
                          <th className="px-3 py-2.5 font-medium">规模</th>
                          <th className="px-3 py-2.5 font-medium">交易执行</th>
                        </>
                      )}
                      <th className="px-3 py-2.5 font-medium">数据状态</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {selectedIndex.funds.map((fund) => {
                      const studying =
                        objectMode === "fund" &&
                        selectedFund?.code === fund.code
                      return (
                        <tr
                          key={fund.code}
                          onClick={() => chooseFund(fund)}
                          className={`cursor-pointer transition-colors ${
                            studying ? "bg-teal-50/60" : "hover:bg-card"
                          }`}
                        >
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <span
                                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${
                                  studying
                                    ? "bg-primary text-white"
                                    : "bg-secondary text-muted-foreground"
                                }`}
                              >
                                {studying ? (
                                  <Check size={12} />
                                ) : (
                                  <Landmark size={12} />
                                )}
                              </span>
                              <div className="min-w-0">
                                <div className="truncate text-xs font-medium text-foreground">
                                  {fund.name}
                                </div>
                                <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                                  {fund.code}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="px-3 py-3 text-xs text-secondary-foreground">
                            {fund.type === "场外指数基金"
                              ? "场外"
                              : fund.type === "主动基金"
                                ? "主动"
                                : fund.type}
                          </td>
                          <td className="px-3 py-3 font-mono text-xs text-muted-foreground">
                            —
                          </td>
                          <td className="px-3 py-3 font-mono text-xs text-muted-foreground">
                            —
                          </td>
                          {moreFundColumns && (
                            <>
                              <td className="px-3 py-3 font-mono text-xs text-muted-foreground">
                                —
                              </td>
                              <td className="px-3 py-3 font-mono text-xs text-muted-foreground">
                                —
                              </td>
                            </>
                          )}
                          <td className="px-3 py-3">
                            <span className="text-xs text-muted-foreground">
                              待接入
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <div className="divide-y divide-border md:hidden">
                {selectedIndex.funds.map((fund) => {
                  const studying =
                    objectMode === "fund" && selectedFund?.code === fund.code
                  return (
                    <button
                      key={fund.code}
                      onClick={() => chooseFund(fund)}
                      className={`flex w-full items-center justify-between gap-3 px-4 py-3 text-left ${
                        studying ? "bg-teal-50/60" : "hover:bg-card"
                      }`}
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-foreground">
                          {fund.name}
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                          <span className="font-mono">{fund.code}</span>
                          <span>·</span>
                          <span>
                            {fund.type === "场外指数基金"
                              ? "场外"
                              : fund.type === "主动基金"
                                ? "主动"
                                : fund.type}
                          </span>
                          <span>·</span>
                          <span>数据待接入</span>
                        </div>
                      </div>
                      <span
                        className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
                          studying
                            ? "bg-primary text-white"
                            : "bg-secondary text-muted-foreground"
                        }`}
                      >
                        {studying ? (
                          <Check size={12} />
                        ) : (
                          <ArrowRight size={12} />
                        )}
                      </span>
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </section>

        <aside className="xl:sticky xl:top-5">
          <section className="overflow-hidden rounded-xl border border-border bg-white shadow-sm">
            <div className="border-b border-border bg-slate-900 p-4 text-white">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-teal-400/15 px-2 py-1 text-xs font-medium text-teal-300">
                      {objectMode === "index" ? "指数研究" : "基金研究"}
                    </span>
                    <span className="truncate text-xs text-slate-400">
                      {marketLabel} / {styleLabel}
                    </span>
                  </div>
                  <h2 className="mt-3 truncate text-base font-semibold">
                    {selectedTitle}
                  </h2>
                  <div className="mt-1 font-mono text-xs text-slate-400">
                    {selectedCode ?? "—"}
                    {objectMode === "fund" && selectedFund
                      ? ` · ${selectedFund.type}`
                      : " · 指数"}
                  </div>
                </div>
                <button
                  onClick={() => setWatching((value) => !value)}
                  disabled={!selectedIndex}
                  className={`flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium ${
                    watching
                      ? "bg-teal-500 text-white"
                      : "bg-white/10 text-slate-200 hover:bg-white/15"
                  }`}
                >
                  {watching ? (
                    <Star size={13} fill="currentColor" />
                  ) : (
                    <ListPlus size={13} />
                  )}
                  {watching ? "已自选" : "加入自选"}
                </button>
              </div>
            </div>

            <div className="flex border-b border-border px-2">
              {([
                ["decision", "综合判断"],
                ["trend", "技术趋势"],
                ["quality", objectMode === "index" ? "指数特征" : "产品质量"],
              ] as Array<[DiagnosisTab, string]>).map(([id, label]) => (
                <button
                  key={id}
                  onClick={() => setDiagnosisTab(id)}
                  className={`flex-1 border-b-2 px-1 py-3 text-xs font-medium ${
                    diagnosisTab === id
                      ? "border-primary text-primary"
                      : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="min-h-80 p-4">
              {diagnosisTab === "decision" && (
                <div>
                  <div className="flex items-start justify-between gap-3 rounded-lg bg-card p-3">
                    <div>
                      <div className="text-xs font-semibold text-foreground">
                        核心结论
                      </div>
                      <div className="mt-1 text-xs leading-5 text-muted-foreground">
                        关键研究数据尚未接入，暂不形成投资方向、买卖时机或仓位结论。
                      </div>
                    </div>
                    {unavailableLabel()}
                  </div>
                  <div className="mt-4 divide-y divide-border rounded-lg border border-border">
                    {(objectMode === "index"
                      ? [
                          ["长期价值", "基本面与估值前景"],
                          ["技术状态", "趋势、动量与波动"],
                          [
                            "宏观关联",
                            profile.supportFactors.join("、") ||
                              "研究因素待建立",
                          ],
                        ]
                      : [
                          ["标的价值", `${selectedIndex?.name} 的长期研究结论`],
                          ["产品质量", "成本、跟踪与执行效率"],
                          ["组合适配", "仓位、相关性与风险预算"],
                        ]
                    ).map(([label, description]) => (
                      <div
                        key={label}
                        className="grid grid-cols-[76px_1fr_auto] items-center gap-2 px-3 py-3"
                      >
                        <span className="text-xs font-medium text-foreground">
                          {label}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {description}
                        </span>
                        <span className="text-xs text-slate-400">—</span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-3 rounded-lg border border-border bg-card px-3 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-xs font-medium text-foreground">
                        下一步观察
                      </span>
                      <span className="text-xs text-slate-400">
                        等待条件变化
                      </span>
                    </div>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      {objectMode === "index"
                        ? "关注基本面、估值、趋势和宏观关联是否出现可验证变化。"
                        : `先确认 ${selectedIndex?.name ?? "跟踪指数"} 的研究结论，再观察产品成本、跟踪质量与组合风险预算。`}
                    </p>
                  </div>
                  {objectMode === "fund" && (
                    <button className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-border py-2.5 text-xs font-medium text-secondary-foreground hover:bg-card">
                      <Landmark size={13} />
                      选择账户持仓
                    </button>
                  )}
                </div>
              )}

              {diagnosisTab === "trend" && (
                <div>
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-xs font-semibold text-foreground">
                        价格与趋势
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        {objectMode === "index"
                          ? "指数复权行情"
                          : "基金复权行情与折溢价"}
                      </div>
                    </div>
                    <div className="flex gap-1">
                      {["3月", "1年", "3年"].map((range) => (
                        <button
                          key={range}
                          disabled
                          className="cursor-not-allowed rounded bg-secondary px-2 py-1 text-xs text-slate-400"
                        >
                          {range}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="mt-3 flex h-40 flex-col items-center justify-center rounded-lg border border-dashed border-border bg-card text-center">
                    <LineChart size={22} className="text-slate-300" />
                    <div className="mt-2 text-xs font-medium text-foreground">
                      暂无有效历史序列
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      数据接入后启用图表与均线开关
                    </div>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    {[
                      "长期趋势 · MA200",
                      "中期趋势 · MA20/60",
                      "短期动量 · MACD/RSI",
                      "波动风险 · 回撤",
                    ].map((metric) => (
                      <div
                        key={metric}
                        className="rounded-lg border border-border px-3 py-2.5"
                      >
                        <div className="text-xs text-secondary-foreground">
                          {metric}
                        </div>
                        <div className="mt-1 text-xs text-slate-400">
                          未计算
                        </div>
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={() => setSignalDetailsOpen((open) => !open)}
                    className="mt-3 flex w-full items-center justify-between rounded-lg border border-border px-3 py-2.5 text-left"
                  >
                    <div>
                      <div className="text-xs font-medium text-foreground">
                        策略条件与信号变化
                      </div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        长期、中期、短期与风险条件
                      </div>
                    </div>
                    <ChevronDown
                      size={13}
                      className={`text-muted-foreground transition-transform ${
                        signalDetailsOpen ? "rotate-180" : ""
                      }`}
                    />
                  </button>
                  {signalDetailsOpen && (
                    <div className="mt-2 divide-y divide-border rounded-lg border border-border">
                      {[
                        ["长期趋势", "价格与 MA200"],
                        ["中期趋势", "MA20 / MA60 与动量"],
                        ["短期状态", "MACD / KDJ / RSI"],
                        [
                          "估值检查",
                          selectedIndex?.valuationMetric ?? "适用指标",
                        ],
                        ["风险检查", "仓位 / 波动 / 集中度"],
                      ].map(([label, rule]) => (
                        <div
                          key={label}
                          className="grid grid-cols-[70px_1fr_auto] items-center gap-2 px-3 py-2.5"
                        >
                          <span className="text-xs font-medium text-foreground">
                            {label}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {rule}
                          </span>
                          <span className="text-xs text-slate-400">—</span>
                        </div>
                      ))}
                      <div className="bg-card px-3 py-2.5 text-xs leading-5 text-muted-foreground">
                        数据接入并完成历史验证后，展示相较上一交易日的信号变化与下一步观察条件。
                      </div>
                    </div>
                  )}
                </div>
              )}

              {diagnosisTab === "quality" && (
                <div>
                  <div className="text-xs font-semibold text-foreground">
                    {objectMode === "index" ? "指数风险暴露" : "基金工具质量"}
                  </div>
                  <div className="mt-3 divide-y divide-border rounded-lg border border-border">
                    {(objectMode === "index"
                      ? [
                          ["市场暴露", selectedIndex?.exposure ?? "—"],
                          ["估值口径", selectedIndex?.valuationMetric ?? "—"],
                          ["行业与风格", "等待指数成分数据"],
                          ["波动与回撤", "等待历史行情"],
                        ]
                      : [
                          ["基金费率", "等待基金合同数据"],
                          ["跟踪质量", "等待完整净值序列"],
                          ["基金规模", "等待最新披露数据"],
                          [
                            selectedFund?.type === "ETF"
                              ? "交易执行"
                              : "申赎规则",
                            "等待产品数据",
                          ],
                        ]
                    ).map(([label, value]) => (
                      <div
                        key={label}
                        className="flex items-center justify-between gap-3 px-3 py-3"
                      >
                        <span className="text-xs font-medium text-foreground">
                          {label}
                        </span>
                        <span className="text-right text-xs text-muted-foreground">
                          {value}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center gap-2 border-t border-border bg-card px-4 py-2.5 text-xs text-muted-foreground">
              <Info size={12} />
              当前仅展示研究结构，不构成投资建议。
            </div>
          </section>
        </aside>
      </div>
    </div>
  )
}

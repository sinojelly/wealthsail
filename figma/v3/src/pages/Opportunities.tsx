import { useMemo, useState } from "react";
import {
  Activity,
  ArrowRight,
  BarChart3,
  Check,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  Database,
  Droplets,
  ExternalLink,
  Gauge,
  Info,
  Landmark,
  LineChart as LineChartIcon,
  Search,
  ShieldAlert,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type DirectionId = "broad" | "dividend" | "growth" | "gold";
type DiagnosisTab = "summary" | "trend" | "quality" | "strategy";

const macroSignals = [
  {
    id: "growth",
    label: "经济增长",
    icon: Activity,
    value: "PMI 50.4",
    change: "较前值 +0.3",
    state: "边际改善",
    detail: "制造业重回扩张区间，生产分项改善快于新订单，复苏强度仍需后续数据确认。",
    source: "国家统计局 · 2026-08-01",
    tone: "positive",
  },
  {
    id: "inflation",
    label: "通胀环境",
    icon: Gauge,
    value: "CPI +0.8%",
    change: "同比 +0.2pct",
    state: "温和回升",
    detail: "居民端通胀温和，PPI 降幅收窄。当前尚未形成明显的再通胀压力。",
    source: "国家统计局 · 2026-08-09",
    tone: "neutral",
  },
  {
    id: "liquidity",
    label: "货币流动性",
    icon: Droplets,
    value: "DR007 1.72%",
    change: "近 20 日 -6bp",
    state: "相对宽松",
    detail: "短端资金价格平稳，信用利差处于历史中低位，为权益估值提供一定支撑。",
    source: "全国银行间同业拆借中心 · 2026-08-22",
    tone: "positive",
  },
  {
    id: "risk",
    label: "市场风险",
    icon: ShieldAlert,
    value: "波动率 18.6",
    change: "近 20 日 +1.4",
    state: "中性偏高",
    detail: "市场波动小幅上行，结构分化扩大。追高风险高于月初，建议分批建立观察仓。",
    source: "中证指数 · 2026-08-22",
    tone: "warning",
  },
] as const;

const directions = {
  broad: {
    name: "中国宽基",
    short: "盈利修复 · 核心资产",
    view: "积极观察",
    score: 76,
    description: "覆盖中国核心权益资产，重点观察盈利修复、估值与市场流动性的共振。",
    driver: "增长边际改善、流动性相对宽松",
    risk: "盈利预期下修或外部风险引发系统性回撤",
    indices: [
      {
        id: "hs300",
        name: "沪深300",
        code: "000300",
        style: "大盘均衡",
        pe: "12.4x",
        percentile: "近 10 年 38%",
        trend: "多头排列",
        volatility: "18.2%",
        score: 81,
      },
      {
        id: "a500",
        name: "中证A500",
        code: "000510",
        style: "行业均衡",
        pe: "14.1x",
        percentile: "发布以来 42%",
        trend: "震荡向上",
        volatility: "19.6%",
        score: 78,
      },
      {
        id: "zz500",
        name: "中证500",
        code: "000905",
        style: "中盘成长",
        pe: "24.8x",
        percentile: "近 10 年 47%",
        trend: "右侧修复",
        volatility: "24.1%",
        score: 70,
      },
    ],
  },
  dividend: {
    name: "红利策略",
    short: "现金流 · 防御配置",
    view: "均衡配置",
    score: 72,
    description: "聚焦稳定现金流与股息回报，在震荡环境中提供相对防御与收益确定性。",
    driver: "低利率环境、现金流确定性偏好",
    risk: "利率快速上行或高股息行业盈利承压",
    indices: [
      {
        id: "csdiv",
        name: "中证红利",
        code: "000922",
        style: "传统高股息",
        pe: "7.6x",
        percentile: "近 10 年 54%",
        trend: "高位震荡",
        volatility: "16.4%",
        score: 75,
      },
      {
        id: "divlow",
        name: "红利低波",
        code: "H30269",
        style: "防御低波",
        pe: "7.2x",
        percentile: "近 10 年 61%",
        trend: "均线粘合",
        volatility: "14.8%",
        score: 73,
      },
    ],
  },
  growth: {
    name: "科技成长",
    short: "产业趋势 · 高弹性",
    view: "谨慎研究",
    score: 64,
    description: "关注创新驱动与产业升级机会，以较高波动换取长期增长弹性。",
    driver: "产业政策、资本开支与盈利兑现",
    risk: "估值较高，利率与盈利预期变化会放大波动",
    indices: [
      {
        id: "chinext",
        name: "创业板指",
        code: "399006",
        style: "新能源成长",
        pe: "29.7x",
        percentile: "近 10 年 31%",
        trend: "底部修复",
        volatility: "28.6%",
        score: 67,
      },
      {
        id: "star50",
        name: "科创50",
        code: "000688",
        style: "硬科技",
        pe: "38.2x",
        percentile: "近 5 年 46%",
        trend: "宽幅震荡",
        volatility: "31.2%",
        score: 61,
      },
    ],
  },
  gold: {
    name: "黄金资产",
    short: "避险对冲 · 全球定价",
    view: "等待回撤",
    score: 59,
    description: "用于对冲实际利率、货币信用与地缘风险，不以企业盈利作为主要定价锚。",
    driver: "实际利率、美元与全球避险需求",
    risk: "美元走强或实际利率快速回升",
    indices: [
      {
        id: "au9999",
        name: "AU9999",
        code: "AU9999",
        style: "人民币黄金",
        pe: "不适用",
        percentile: "价格近 10 年 86%",
        trend: "高位整理",
        volatility: "17.1%",
        score: 59,
      },
    ],
  },
} satisfies Record<
  DirectionId,
  {
    name: string;
    short: string;
    view: string;
    score: number;
    description: string;
    driver: string;
    risk: string;
    indices: Array<{
      id: string;
      name: string;
      code: string;
      style: string;
      pe: string;
      percentile: string;
      trend: string;
      volatility: string;
      score: number;
    }>;
  }
>;

const fundCatalog: Record<
  string,
  Array<{
    code: string;
    name: string;
    type: string;
    fee: string;
    tracking: string;
    scale: string;
    liquidity: string;
    premium: string;
    quality: number;
  }>
> = {
  hs300: [
    { code: "510300", name: "华泰柏瑞沪深300ETF", type: "场内 ETF", fee: "0.20%", tracking: "0.11%", scale: "1,246亿", liquidity: "日均 32.6亿", premium: "+0.03%", quality: 92 },
    { code: "510330", name: "华夏沪深300ETF", type: "场内 ETF", fee: "0.20%", tracking: "0.13%", scale: "423亿", liquidity: "日均 8.4亿", premium: "-0.02%", quality: 88 },
    { code: "110020", name: "易方达沪深300ETF联接A", type: "场外基金", fee: "0.15%", tracking: "0.18%", scale: "98亿", liquidity: "T+1 确认", premium: "不适用", quality: 84 },
  ],
  a500: [
    { code: "512050", name: "华夏中证A500ETF", type: "场内 ETF", fee: "0.15%", tracking: "0.16%", scale: "186亿", liquidity: "日均 12.1亿", premium: "+0.05%", quality: 89 },
    { code: "159338", name: "国泰中证A500ETF", type: "场内 ETF", fee: "0.15%", tracking: "0.17%", scale: "121亿", liquidity: "日均 7.3亿", premium: "+0.02%", quality: 86 },
    { code: "022448", name: "摩根中证A500指数增强A", type: "场外基金", fee: "0.50%", tracking: "增强策略", scale: "24亿", liquidity: "T+1 确认", premium: "不适用", quality: 76 },
  ],
  zz500: [
    { code: "510500", name: "南方中证500ETF", type: "场内 ETF", fee: "0.20%", tracking: "0.14%", scale: "792亿", liquidity: "日均 18.9亿", premium: "+0.04%", quality: 91 },
    { code: "510580", name: "易方达中证500ETF", type: "场内 ETF", fee: "0.15%", tracking: "0.19%", scale: "73亿", liquidity: "日均 2.1亿", premium: "-0.03%", quality: 80 },
  ],
  csdiv: [
    { code: "515180", name: "易方达中证红利ETF", type: "场内 ETF", fee: "0.20%", tracking: "0.15%", scale: "68亿", liquidity: "日均 2.8亿", premium: "+0.02%", quality: 86 },
    { code: "090010", name: "大成中证红利指数A", type: "场外基金", fee: "0.75%", tracking: "0.42%", scale: "46亿", liquidity: "T+1 确认", premium: "不适用", quality: 73 },
  ],
  divlow: [
    { code: "512890", name: "华泰柏瑞红利低波ETF", type: "场内 ETF", fee: "0.50%", tracking: "0.18%", scale: "214亿", liquidity: "日均 5.6亿", premium: "+0.01%", quality: 87 },
  ],
  chinext: [
    { code: "159915", name: "易方达创业板ETF", type: "场内 ETF", fee: "0.15%", tracking: "0.12%", scale: "731亿", liquidity: "日均 20.8亿", premium: "-0.01%", quality: 91 },
    { code: "110026", name: "易方达创业板ETF联接A", type: "场外基金", fee: "0.15%", tracking: "0.21%", scale: "96亿", liquidity: "T+1 确认", premium: "不适用", quality: 82 },
  ],
  star50: [
    { code: "588000", name: "华夏上证科创板50ETF", type: "场内 ETF", fee: "0.50%", tracking: "0.13%", scale: "702亿", liquidity: "日均 17.2亿", premium: "+0.06%", quality: 88 },
  ],
  au9999: [
    { code: "518880", name: "华安黄金ETF", type: "场内 ETF", fee: "0.50%", tracking: "0.09%", scale: "312亿", liquidity: "日均 6.4亿", premium: "+0.08%", quality: 90 },
    { code: "000216", name: "华安黄金ETF联接A", type: "场外基金", fee: "0.50%", tracking: "0.23%", scale: "129亿", liquidity: "T+1 确认", premium: "不适用", quality: 81 },
  ],
};

const trendData = [
  { date: "07/15", fund: 97.8, ma20: 98.5 },
  { date: "07/19", fund: 98.6, ma20: 98.7 },
  { date: "07/23", fund: 99.1, ma20: 98.9 },
  { date: "07/29", fund: 98.7, ma20: 99.0 },
  { date: "08/02", fund: 100.2, ma20: 99.2 },
  { date: "08/06", fund: 101.4, ma20: 99.6 },
  { date: "08/12", fund: 102.1, ma20: 100.1 },
  { date: "08/16", fund: 101.7, ma20: 100.5 },
  { date: "08/20", fund: 103.2, ma20: 101.1 },
  { date: "08/22", fund: 104.1, ma20: 101.7 },
];

function ScoreRing({ value }: { value: number }) {
  return (
    <div className="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-secondary">
      <span className="font-mono text-sm font-semibold text-primary">{value}</span>
      <span className="absolute bottom-1 text-[8px] uppercase tracking-wide text-muted-foreground">
        score
      </span>
    </div>
  );
}

export default function Opportunities() {
  const [directionId, setDirectionId] = useState<DirectionId>("broad");
  const direction = directions[directionId];
  const [indexId, setIndexId] = useState(direction.indices[0].id);
  const [selectedFundCode, setSelectedFundCode] = useState(
    fundCatalog[direction.indices[0].id][0].code,
  );
  const [activeMacro, setActiveMacro] = useState("growth");
  const [tab, setTab] = useState<DiagnosisTab>("summary");
  const [query, setQuery] = useState("");

  const selectedIndex =
    direction.indices.find((item) => item.id === indexId) ?? direction.indices[0];
  const funds = fundCatalog[selectedIndex.id] ?? [];
  const filteredFunds = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return funds;
    return funds.filter(
      (fund) =>
        fund.name.toLowerCase().includes(normalized) ||
        fund.code.toLowerCase().includes(normalized),
    );
  }, [funds, query]);
  const selectedFund =
    funds.find((fund) => fund.code === selectedFundCode) ?? funds[0];
  const macro = macroSignals.find((item) => item.id === activeMacro)!;

  const chooseDirection = (id: DirectionId) => {
    const nextDirection = directions[id];
    const nextIndex = nextDirection.indices[0];
    setDirectionId(id);
    setIndexId(nextIndex.id);
    setSelectedFundCode(fundCatalog[nextIndex.id][0].code);
    setQuery("");
  };

  const chooseIndex = (id: string) => {
    setIndexId(id);
    setSelectedFundCode(fundCatalog[id][0].code);
    setQuery("");
  };

  return (
    <div className="mx-auto max-w-screen-2xl space-y-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-primary">
            <Sparkles size={14} />
            DISCOVERY WORKBENCH
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            投资机会中心
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            从宏观环境出发，逐层研究投资方向、指数与基金，并独立判断长期价值和买卖时机。
          </p>
        </div>
        <div className="flex items-center gap-3 rounded-lg border border-border bg-white px-4 py-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-emerald-50 text-success">
            <Database size={15} />
          </div>
          <div>
            <div className="text-xs font-medium text-foreground">研究数据已更新</div>
            <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
              2026-08-23 07:45 CST · 覆盖率 92%
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 overflow-x-auto rounded-xl border border-border bg-white p-2">
        {["宏观环境", "投资方向", "指数比较", "基金选择", "决策诊断"].map(
          (step, index) => (
            <div key={step} className="flex min-w-fit flex-1 items-center">
              <div
                className={`flex flex-1 items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium ${
                  index === 4
                    ? "bg-primary text-primary-foreground"
                    : "bg-secondary text-secondary-foreground"
                }`}
              >
                <span
                  className={`flex h-5 w-5 items-center justify-center rounded-full font-mono text-[10px] ${
                    index === 4 ? "bg-white/20" : "bg-white"
                  }`}
                >
                  {index + 1}
                </span>
                {step}
              </div>
              {index < 4 && (
                <ArrowRight
                  size={13}
                  className="mx-2 shrink-0 text-muted-foreground"
                />
              )}
            </div>
          ),
        )}
      </div>

      <section className="space-y-3">
        <div className="flex items-end justify-between">
          <div>
            <h2 className="text-base font-semibold text-foreground">宏观经济观察</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              仅呈现与资产配置相关的关键变化，所有结论均保留来源和反方风险。
            </p>
          </div>
          <button className="flex items-center gap-1 text-xs font-medium text-primary">
            查看数据方法
            <ExternalLink size={12} />
          </button>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {macroSignals.map((item) => {
            const Icon = item.icon;
            const active = activeMacro === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setActiveMacro(item.id)}
                className={`rounded-xl border bg-white p-4 text-left transition-all ${
                  active
                    ? "border-primary shadow-sm ring-1 ring-primary/10"
                    : "border-border hover:border-slate-300"
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                    <Icon size={14} className={active ? "text-primary" : ""} />
                    {item.label}
                  </div>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                      item.tone === "positive"
                        ? "bg-emerald-50 text-emerald-700"
                        : item.tone === "warning"
                          ? "bg-amber-50 text-amber-700"
                          : "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {item.state}
                  </span>
                </div>
                <div className="mt-3 flex items-end justify-between">
                  <span className="font-mono text-lg font-semibold text-foreground">
                    {item.value}
                  </span>
                  <span className="font-mono text-[11px] text-muted-foreground">
                    {item.change}
                  </span>
                </div>
                <div className="mt-3 border-t border-border pt-3 text-[11px] text-muted-foreground">
                  {item.source}
                </div>
              </button>
            );
          })}
        </div>
        <div className="flex items-start gap-3 rounded-lg border border-sky-100 bg-sky-50/70 px-4 py-3">
          <Info size={15} className="mt-0.5 shrink-0 text-sky-600" />
          <div className="text-xs leading-5 text-slate-600">
            <span className="font-semibold text-slate-800">{macro.label}：</span>
            {macro.detail}
            <span className="ml-2 font-mono text-[10px] text-slate-400">
              {macro.source}
            </span>
          </div>
        </div>
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold text-foreground">选择投资方向</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            系统结论仅作研究导航，你可以不受宏观判断限制，自由切换任意方向。
          </p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {(Object.keys(directions) as DirectionId[]).map((id) => {
            const item = directions[id];
            const active = id === directionId;
            return (
              <button
                key={id}
                onClick={() => chooseDirection(id)}
                className={`group rounded-xl border p-4 text-left transition-all ${
                  active
                    ? "border-primary bg-teal-50/50 shadow-sm"
                    : "border-border bg-white hover:-translate-y-0.5 hover:shadow-sm"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-foreground">{item.name}</span>
                      {active && (
                        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-primary text-white">
                          <Check size={10} />
                        </span>
                      )}
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">{item.short}</div>
                  </div>
                  <ScoreRing value={item.score} />
                </div>
                <div className="mt-4 flex items-center justify-between border-t border-border/70 pt-3">
                  <span className="text-[11px] text-muted-foreground">研究状态</span>
                  <span className="text-xs font-medium text-primary">{item.view}</span>
                </div>
              </button>
            );
          })}
        </div>
        <div className="grid gap-4 rounded-xl border border-border bg-white p-5 lg:grid-cols-[1.25fr_1fr]">
          <div>
            <div className="text-sm font-semibold text-foreground">{direction.name}研究概要</div>
            <p className="mt-2 text-sm leading-6 text-secondary-foreground">
              {direction.description}
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg bg-emerald-50 p-3">
              <div className="mb-1 text-[11px] font-medium text-emerald-700">主要驱动</div>
              <div className="text-xs leading-5 text-emerald-900">{direction.driver}</div>
            </div>
            <div className="rounded-lg bg-amber-50 p-3">
              <div className="mb-1 text-[11px] font-medium text-amber-700">反方风险</div>
              <div className="text-xs leading-5 text-amber-900">{direction.risk}</div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(420px,0.85fr)]">
        <div className="overflow-hidden rounded-xl border border-border bg-white">
          <div className="flex items-center justify-between border-b border-border px-5 py-4">
            <div>
              <h2 className="text-base font-semibold text-foreground">相关指数比较</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                先决定承担哪类市场风险，再比较获得该敞口的具体工具。
              </p>
            </div>
            <span className="rounded-full bg-secondary px-2.5 py-1 text-[11px] text-secondary-foreground">
              {direction.indices.length} 个候选
            </span>
          </div>
          <div className="divide-y divide-border">
            {direction.indices.map((item) => {
              const active = item.id === selectedIndex.id;
              return (
                <button
                  key={item.id}
                  onClick={() => chooseIndex(item.id)}
                  className={`grid w-full grid-cols-[minmax(140px,1.2fr)_repeat(3,minmax(88px,1fr))_48px] items-center gap-3 px-5 py-4 text-left transition-colors ${
                    active ? "bg-teal-50/60" : "hover:bg-card"
                  }`}
                >
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-foreground">{item.name}</span>
                      {active && (
                        <span className="rounded bg-primary px-1.5 py-0.5 text-[9px] font-medium text-white">
                          已选择
                        </span>
                      )}
                    </div>
                    <div className="mt-1 font-mono text-[11px] text-muted-foreground">
                      {item.code} · {item.style}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground">估值</div>
                    <div className="mt-1 font-mono text-xs font-medium text-foreground">
                      {item.pe}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground">历史位置</div>
                    <div className="mt-1 font-mono text-xs font-medium text-foreground">
                      {item.percentile}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground">趋势 / 波动</div>
                    <div className="mt-1 text-xs font-medium text-foreground">
                      {item.trend}
                    </div>
                    <div className="font-mono text-[10px] text-muted-foreground">
                      {item.volatility}
                    </div>
                  </div>
                  <ScoreRing value={item.score} />
                </button>
              );
            })}
          </div>
          <div className="flex items-center gap-2 border-t border-border bg-card px-5 py-3 text-[11px] text-muted-foreground">
            <Info size={12} />
            估值分位与价格区间位置口径不同；黄金等资产不使用股票市盈率评价。
          </div>
        </div>

        <div className="overflow-hidden rounded-xl border border-border bg-slate-900 text-white">
          <div className="flex items-start justify-between p-5">
            <div>
              <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400">
                Selected exposure
              </div>
              <div className="mt-2 text-xl font-semibold">{selectedIndex.name}</div>
              <div className="mt-1 font-mono text-xs text-slate-400">
                {selectedIndex.code} · 截至 2026-08-22
              </div>
            </div>
            <div className="rounded-lg bg-white/10 p-2.5 text-teal-300">
              <LineChartIcon size={20} />
            </div>
          </div>
          <div className="h-40 px-2">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trendData}>
                <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                <XAxis
                  dataKey="date"
                  tick={{ fill: "#94A3B8", fontSize: 9 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis hide domain={["dataMin - 1", "dataMax + 1"]} />
                <Tooltip
                  contentStyle={{
                    background: "#0F172A",
                    border: "1px solid #334155",
                    borderRadius: 8,
                    fontSize: 11,
                  }}
                  labelStyle={{ color: "#94A3B8" }}
                />
                <Line
                  type="monotone"
                  dataKey="fund"
                  stroke="#2DD4BF"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="ma20"
                  stroke="#64748B"
                  strokeWidth={1.5}
                  strokeDasharray="4 4"
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="grid grid-cols-3 border-t border-white/10">
            {[
              ["近 20 日", "+4.8%"],
              ["趋势状态", selectedIndex.trend],
              ["年化波动", selectedIndex.volatility],
            ].map(([label, value]) => (
              <div key={label} className="border-r border-white/10 px-4 py-3 last:border-r-0">
                <div className="text-[10px] text-slate-500">{label}</div>
                <div className="mt-1 font-mono text-xs font-medium text-slate-100">{value}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-xl border border-border bg-white">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-foreground">
              跟踪 {selectedIndex.name} 的基金候选
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              同指数产品优先比较费用、跟踪质量、规模与执行成本。
            </p>
          </div>
          <div className="relative">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
            />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索基金名称或代码"
              className="w-60 rounded-lg border border-border bg-white py-2 pl-9 pr-3 text-xs text-foreground outline-none transition-colors placeholder:text-muted-foreground focus:border-primary"
            />
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-left">
            <thead className="bg-card text-[11px] font-medium text-muted-foreground">
              <tr>
                <th className="px-5 py-3 font-medium">基金</th>
                <th className="px-4 py-3 font-medium">类型</th>
                <th className="px-4 py-3 font-medium">综合费率</th>
                <th className="px-4 py-3 font-medium">跟踪误差</th>
                <th className="px-4 py-3 font-medium">基金规模</th>
                <th className="px-4 py-3 font-medium">流动性</th>
                <th className="px-4 py-3 font-medium">折溢价</th>
                <th className="px-4 py-3 text-right font-medium">质量分</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filteredFunds.map((fund) => {
                const active = selectedFund.code === fund.code;
                return (
                  <tr
                    key={fund.code}
                    onClick={() => {
                      setSelectedFundCode(fund.code);
                      setTab("summary");
                    }}
                    className={`cursor-pointer transition-colors ${
                      active ? "bg-teal-50/60" : "hover:bg-card"
                    }`}
                  >
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-3">
                        <span
                          className={`flex h-7 w-7 items-center justify-center rounded-md ${
                            active
                              ? "bg-primary text-white"
                              : "bg-secondary text-muted-foreground"
                          }`}
                        >
                          {active ? <Check size={13} /> : <Landmark size={13} />}
                        </span>
                        <div>
                          <div className="text-xs font-medium text-foreground">{fund.name}</div>
                          <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                            {fund.code}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3.5 text-xs text-secondary-foreground">{fund.type}</td>
                    <td className="px-4 py-3.5 font-mono text-xs text-foreground">{fund.fee}</td>
                    <td className="px-4 py-3.5 font-mono text-xs text-foreground">{fund.tracking}</td>
                    <td className="px-4 py-3.5 font-mono text-xs text-foreground">{fund.scale}</td>
                    <td className="px-4 py-3.5 font-mono text-xs text-foreground">{fund.liquidity}</td>
                    <td className="px-4 py-3.5 font-mono text-xs text-foreground">{fund.premium}</td>
                    <td className="px-4 py-3.5 text-right">
                      <span className="inline-flex rounded-full bg-emerald-50 px-2 py-1 font-mono text-xs font-semibold text-emerald-700">
                        {fund.quality}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {filteredFunds.length === 0 && (
            <div className="px-5 py-10 text-center text-sm text-muted-foreground">
              当前指数候选中未找到匹配基金。正式数据接入后可搜索全市场产品。
            </div>
          )}
        </div>
      </section>

      <section className="overflow-hidden rounded-xl border border-border bg-white">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-white">
              <BarChart3 size={18} />
            </div>
            <div>
              <h2 className="text-base font-semibold text-foreground">{selectedFund.name}</h2>
              <div className="mt-0.5 flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
                <span>{selectedFund.code}</span>
                <span>·</span>
                <span>{selectedFund.type}</span>
                <span>·</span>
                <span>数据 2026-08-22</span>
              </div>
            </div>
          </div>
          <button className="flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-xs font-medium text-white transition-colors hover:bg-teal-700">
            加入研究清单
            <ArrowRight size={13} />
          </button>
        </div>
        <div className="flex gap-1 overflow-x-auto border-b border-border px-5 pt-2">
          {(
            [
              ["summary", "综合判断"],
              ["trend", "趋势分析"],
              ["quality", "基金质量"],
              ["strategy", "策略验证"],
            ] as Array<[DiagnosisTab, string]>
          ).map(([id, label]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`border-b-2 px-4 py-2.5 text-xs font-medium transition-colors ${
                tab === id
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="p-5">
          {tab === "summary" && (
            <div className="grid gap-4 md:grid-cols-3">
              <div className="rounded-xl border border-border p-4">
                <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                  <CircleDollarSign size={14} />
                  长期配置价值
                </div>
                <div className="mt-4 flex items-center justify-between">
                  <span className="text-lg font-semibold text-foreground">值得研究</span>
                  <span className="rounded-full bg-emerald-50 px-2 py-1 text-[10px] font-medium text-emerald-700">
                    证据较充分
                  </span>
                </div>
                <p className="mt-3 text-xs leading-5 text-muted-foreground">
                  跟踪主流宽基，费率与跟踪质量在同类中有竞争力，适合作为核心权益工具候选。
                </p>
              </div>
              <div className="rounded-xl border border-border p-4">
                <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                  <TrendingUp size={14} />
                  近期技术状态
                </div>
                <div className="mt-4 flex items-center justify-between">
                  <span className="text-lg font-semibold text-foreground">震荡偏强</span>
                  <span className="rounded-full bg-sky-50 px-2 py-1 text-[10px] font-medium text-sky-700">
                    MA20 上方
                  </span>
                </div>
                <p className="mt-3 text-xs leading-5 text-muted-foreground">
                  中期均线向上，短线动量偏热。趋势未破坏，但不宜仅凭单一指标追涨。
                </p>
              </div>
              <div className="rounded-xl border border-border p-4">
                <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                  <ShieldAlert size={14} />
                  当前组合可承担风险
                </div>
                <div className="mt-4 flex items-center justify-between">
                  <span className="text-lg font-semibold text-foreground">尚未评估</span>
                  <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-medium text-slate-600">
                    需连接持仓
                  </span>
                </div>
                <p className="mt-3 text-xs leading-5 text-muted-foreground">
                  尚未运行与当前组合的相关性、集中度和回撤预算检查，不生成仓位建议。
                </p>
              </div>
            </div>
          )}
          {tab === "trend" && (
            <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
              <div className="h-64 rounded-xl border border-border p-3">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={trendData}>
                    <CartesianGrid stroke="var(--color-border)" vertical={false} />
                    <XAxis dataKey="date" tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
                    <YAxis domain={["dataMin - 1", "dataMax + 1"]} tick={{ fontSize: 10 }} axisLine={false} tickLine={false} />
                    <Tooltip />
                    <Line type="monotone" dataKey="fund" name="复权价格" stroke="var(--color-primary)" strokeWidth={2} dot={false} />
                    <Line type="monotone" dataKey="ma20" name="MA20" stroke="var(--color-muted-foreground)" strokeDasharray="4 4" dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {[
                  ["MACD", "金叉 · 柱体收敛"],
                  ["KDJ", "K 78.2 · 偏热"],
                  ["RSI(14)", "63.7 · 中性偏强"],
                  ["MA", "5 > 20 > 60"],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-xl bg-card p-4">
                    <div className="font-mono text-[11px] text-muted-foreground">{label}</div>
                    <div className="mt-2 text-xs font-medium text-foreground">{value}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {tab === "quality" && (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[
                ["综合费率", selectedFund.fee, "同类成本比较"],
                ["跟踪误差", selectedFund.tracking, "近 1 年年化"],
                ["基金规模", selectedFund.scale, "规模稳定性良好"],
                ["交易流动性", selectedFund.liquidity, selectedFund.premium],
              ].map(([label, value, note]) => (
                <div key={label} className="rounded-xl border border-border p-4">
                  <div className="text-xs text-muted-foreground">{label}</div>
                  <div className="mt-3 font-mono text-lg font-semibold text-foreground">{value}</div>
                  <div className="mt-2 text-[11px] text-muted-foreground">{note}</div>
                </div>
              ))}
            </div>
          )}
          {tab === "strategy" && (
            <div className="flex items-start gap-4 rounded-xl border border-dashed border-border bg-card p-5">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-white text-muted-foreground">
                <Clock3 size={18} />
              </div>
              <div>
                <div className="text-sm font-semibold text-foreground">策略验证尚未接入</div>
                <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">
                  后续将展示可复现的历史信号、基准对照、交易成本与最大回撤。历史表现不代表未来收益，在计算完成前不会生成模拟胜率。
                </p>
                <button className="mt-3 flex items-center gap-1 text-xs font-medium text-primary">
                  查看计划中的验证口径
                  <ChevronDown size={12} />
                </button>
              </div>
            </div>
          )}
        </div>
        <div className="flex items-start gap-2 border-t border-border bg-card px-5 py-3 text-[11px] leading-5 text-muted-foreground">
          <Info size={12} className="mt-1 shrink-0" />
          页面数据为产品交互示例，不构成投资建议。正式版需接入可验证的数据源、历史可得时间与计算版本；缺失数据保持“尚未评估”。
        </div>
      </section>
    </div>
  );
}

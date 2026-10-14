import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MacroContextStrip from "./components/MacroContextStrip";
import type {
  Direction,
  OpportunityContext,
  OpportunitySeries,
} from "../../api/investment";

const directions = [
  {
    id: "a-share:broad",
    marketId: "a-share",
    marketLabel: "A股",
    label: "宽基",
    styleId: "broad",
    framework: "盈利与风险",
  },
  {
    id: "a-share:growth",
    marketId: "a-share",
    marketLabel: "A股",
    label: "成长",
    styleId: "growth",
    framework: "盈利与利率",
  },
  {
    id: "gold:physical",
    marketId: "gold",
    marketLabel: "黄金",
    label: "黄金现货",
    styleId: "physical",
    framework: "实际利率",
  },
  {
    id: "us:broad",
    marketId: "us",
    marketLabel: "美股",
    label: "宽基",
    styleId: "broad",
    framework: "本地盈利",
  },
] satisfies Direction[];

const context: OpportunityContext = {
  state: "UNVERIFIED",
  items: [],
  radar: {
    methodVersion: "opportunity-radar-v1",
    asOf: "2026-10-14T04:00:00Z",
    headline: {
      id: "rate-review",
      priority: "CRITICAL",
      kind: "RISK",
      summary: "实际利率出现值得复核的变化。",
      impact: "核查黄金与成长资产的利率敏感度。",
      evidenceIds: ["US_T10_REAL"],
      timeScale: "每日盘后",
      relatedDirectionIds: ["gold:physical"],
    },
    questions: [
      {
        id: "CHANGE",
        label: "发生了什么重要变化？",
        state: "PARTIAL",
        summary: "利率变化需要及时复核。",
        critical: true,
      },
      {
        id: "IMPACT",
        label: "对当前方向有什么影响？",
        state: "PARTIAL",
        summary: "需同时检查盈利修复与折现率。",
        critical: false,
      },
      {
        id: "QUALIFICATION",
        label: "哪些研究条件已满足？",
        state: "UNKNOWN",
        summary: "当前指数估值证据不足。",
        critical: false,
      },
      {
        id: "PERSONAL",
        label: "结合持仓应关注什么？",
        state: "UNKNOWN",
        summary: "当前账户现金及预算未确认。",
        critical: false,
      },
    ],
    changes: [
      {
        id: "important",
        priority: "ATTENTION",
        kind: "OBSERVATION",
        summary: "优先关注的已确认变化",
        impact: "核查相关盈利条件",
        evidenceIds: ["PMI"],
        timeScale: "月度",
        relatedDirectionIds: [],
      },
      {
        id: "ordinary",
        priority: "INFO",
        kind: "OBSERVATION",
        summary: "常规数据更新",
        impact: "保留事实观察",
        evidenceIds: ["CPI_YOY"],
        timeScale: "月度",
        relatedDirectionIds: ["us:broad"],
      },
      {
        id: "third",
        priority: "INFO",
        kind: "GAP",
        summary: "第三条普通变化",
        impact: "等待证据",
        evidenceIds: [],
        timeScale: "月度",
        relatedDirectionIds: [],
      },
    ],
    sections: [
      {
        id: "MACRO",
        label: "宏观周期",
        state: "PARTIAL",
        summary: "制造业与价格观察",
        criticalImpact: null,
        missingEvidence: ["盈利结构"],
      },
      {
        id: "INTEREST_CREDIT",
        label: "利率与信用",
        state: "PARTIAL",
        summary: "利率与信用分别核查",
        criticalImpact: "优先复核实际利率变化",
        missingEvidence: ["人民币利率"],
      },
      {
        id: "MARKET",
        label: "指数与市场",
        state: "UNKNOWN",
        summary: "当前指数等待证据",
        criticalImpact: null,
        missingEvidence: ["盈利与市场宽度"],
      },
      {
        id: "GLOBAL",
        label: "全球事件",
        state: "UNKNOWN",
        summary: "尚无可靠新事件",
        criticalImpact: null,
        missingEvidence: [],
      },
    ],
  },
  series: [
    {
      code: "US_T10_REAL",
      label: "美国10年期实际利率",
      frequency: "DAILY",
      unit: "%",
      value: -0.5,
      asOf: "2026-10-13",
      sourceUri: "https://home.treasury.gov/real",
      sourceName: "美国财政部",
      publishedAt: null,
      retrievedAt: "2026-10-14T03:00:00Z",
      availableAt: "2026-10-14T03:00:00Z",
      timeBasis: "RETRIEVAL_ONLY",
      methodVersion: "rates-v1",
      state: "READY",
      changes: {
        previousValue: -0.6,
        change5Samples: 40,
        change20Samples: null,
        changeUnit: "bp",
        median: null,
        percentile: null,
        sampleCount: 6,
      },
      history: [
        { date: "2026-10-12", value: -0.6, state: "READY" },
        { date: "2026-10-13", value: -0.5, state: "READY" },
      ],
    },
    {
      code: "CN_T10",
      label: "中国10年期国债收益率",
      frequency: "DAILY",
      unit: "%",
      value: null,
      asOf: null,
      sourceUri: "https://yield.chinabond.com.cn/",
      sourceName: "中债",
      publishedAt: null,
      retrievedAt: null,
      availableAt: null,
      timeBasis: "UNKNOWN",
      methodVersion: "rates-v1",
      state: "PERMISSION_REQUIRED",
      changes: {
        previousValue: null,
        change5Samples: null,
        change20Samples: null,
        changeUnit: "bp",
        median: null,
        percentile: null,
        sampleCount: 0,
      },
      history: [],
    },
  ],
  marketEvidence: [
    {
      indexId: "hs300",
      label: "沪深300",
      state: "PARTIAL",
      trend: {
        state: "READY",
        ma200: 4000,
        momentum63: 0.03,
        distanceMA200: 0.02,
      },
      risk: { state: "READY", volatility20: 0.12, maxDrawdown: -0.2 },
      valuation: { state: "UNKNOWN" },
      earnings: { state: "UNKNOWN", summary: "尚无可核验盈利数据" },
      missingEvidence: ["盈利与估值覆盖"],
      sourceUri: null,
      asOf: "2026-10-13",
    },
  ],
  directionModel: {
    methodVersion: "opportunity-radar-v1",
    state: "PARTIAL",
    supports: [
      {
        label: "制造业观察",
        summary: "制造业变化提示核查盈利",
        evidenceIds: ["PMI"],
        state: "PARTIAL",
      },
    ],
    counterFactors: [
      {
        label: "估值逆风",
        summary: "盈利改善可能被估值抵消",
        evidenceIds: [],
        state: "UNKNOWN",
      },
    ],
    watchFor: ["真实盈利与融资成本"],
    requiredEvidence: ["指数估值"],
    missingEvidence: ["人民币利率"],
  },
  decision: {
    state: "WAIT",
    marketState: "WAIT",
    personalState: "UNKNOWN",
    summary: "等待必要证据，不形成建仓结论。",
    gates: [
      {
        id: "validation",
        label: "真实时点样本外验证",
        state: "UNKNOWN",
        reason: "没有真实历史vintage证明",
      },
    ],
    validation: {
      state: "UNAVAILABLE",
      summary: "尚未认证策略",
      methodVersion: "trend-pit-v1",
    },
    nextChecks: ["先确认账户风险预算"],
  },
  portfolio: {
    state: "UNKNOWN",
    heldSelected: null,
    selectedWeight: null,
    sameIndexWeight: null,
    cashRatio: null,
    asOf: null,
    summary: "持仓与预算尚未确认。",
    missingEvidence: ["完整估值与现金"],
  },
  notices: [
    {
      id: "notice-1",
      title: "研究条件发生变化",
      summary: "证据覆盖变化，需重新核查。",
      severity: "ATTENTION",
      createdAt: "2026-10-14T04:00:00Z",
      readAt: null,
    },
  ],
};

async function expand() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /市场环境概览/ }));
  return user;
}

it("shows the four decision questions while collapsed and highlights only backend critical impact", () => {
  render(
    <MacroContextStrip
      data={context}
      error={false}
      selectedDirection={directions[0]}
    />,
  );
  for (const question of context.radar!.questions)
    expect(screen.getByText(question.label)).toBeInTheDocument();
  expect(screen.getByText(context.radar!.headline.impact)).toHaveClass(
    "op-critical-impact",
  );
  expect(screen.getByText(context.radar!.questions[0].summary)).toHaveClass(
    "op-critical-impact",
  );
  expect(screen.getByText(context.radar!.questions[1].summary)).not.toHaveClass(
    "op-critical-impact",
  );
  expect(screen.getByText("优先关注的已确认变化")).toBeInTheDocument();
  expect(screen.queryByText("第三条普通变化")).not.toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("expands the complete backend-prioritized changes and labels required evidence in Chinese", async () => {
  render(
    <MacroContextStrip
      data={{
        ...context,
        directionModel: {
          ...context.directionModel!,
          requiredEvidence: [
            "INDEX_TREND",
            "INDEX_VALUATION",
            "INDEX_EARNINGS",
          ],
        },
      }}
      error={false}
    />,
  );
  expect(screen.queryByText("第三条普通变化")).not.toBeInTheDocument();
  await expand();
  const changes = screen.getByRole("region", { name: "重要条件变化" });
  const items = within(changes).getAllByRole("listitem");
  expect(items[0]).toHaveTextContent("优先关注的已确认变化");
  expect(items[2]).toHaveTextContent("第三条普通变化");
  expect(
    screen.getByText("必要证据：当前指数趋势、当前指数估值、企业盈利证据"),
  ).toBeInTheDocument();
});

it("shows current-market directions and a critical reliable cross-market link before all directions", async () => {
  const select = vi.fn();
  render(
    <MacroContextStrip
      data={context}
      error={false}
      selectedDirection={directions[0]}
      directions={directions}
      onDirectionSelect={select}
    />,
  );
  const user = await expand();
  expect(
    screen.getByRole("button", { name: "研究 A股 · 成长" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "研究 黄金 · 黄金现货" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "研究 美股 · 宽基" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "全部研究方向" }));
  expect(select).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "研究 美股 · 宽基" }));
  expect(select).toHaveBeenCalledWith("us:broad");
});

it("does not guess a current market when a directly searched fund has no direction", async () => {
  const select = vi.fn();
  render(
    <MacroContextStrip
      data={{
        ...context,
        radar: {
          ...context.radar!,
          headline: {
            ...context.radar!.headline,
            priority: "INFO",
            relatedDirectionIds: [],
          },
        },
      }}
      error={false}
      directions={directions}
      onDirectionSelect={select}
    />,
  );
  const user = await expand();
  const menu = screen.getByRole("region", { name: "宏观方向研究" });
  expect(
    within(menu).queryByRole("button", { name: "研究 A股 · 宽基" }),
  ).not.toBeInTheDocument();
  await user.click(within(menu).getByRole("button", { name: "全部研究方向" }));
  expect(
    within(menu).getByRole("button", { name: "研究 A股 · 宽基" }),
  ).toBeInTheDocument();
  expect(select).not.toHaveBeenCalled();
});

it("keeps basis-point sample changes, negative yields and restricted sources honest", async () => {
  render(<MacroContextStrip data={context} error={false} />);
  await expand();
  const rates = screen.getByRole("region", { name: "关键变量证据" });
  expect(within(rates).getByText("-0.5 %")).toBeInTheDocument();
  expect(
    within(rates).getByText(/近5个可比较观察样本：\+40.00 基点/),
  ).toBeInTheDocument();
  expect(within(rates).getByText(/实际公布时刻未确认/)).toBeInTheDocument();
  expect(within(rates).getByText(/获取时点可得/)).toBeInTheDocument();
  expect(
    within(rates).getByText("需要来源授权，当前未自动采集。"),
  ).toBeInTheDocument();
  expect(
    within(rates).getByRole("link", { name: "查看中国10年期国债收益率来源" }),
  ).toHaveAttribute("href", "https://yield.chinabond.com.cn/");
});

it("keeps strategy validation, market gates and unknown holdings separate", async () => {
  render(<MacroContextStrip data={context} error={false} />);
  await expand();
  const decision = screen.getByRole("region", { name: "策略与个人持仓" });
  expect(
    within(decision).getByText("等待必要证据，不形成建仓结论。"),
  ).toBeInTheDocument();
  expect(within(decision).getByText("尚未认证策略")).toBeInTheDocument();
  expect(within(decision).getByText("持仓状态：待确认")).toBeInTheDocument();
  expect(within(decision).queryByText("未持有")).not.toBeInTheDocument();
  expect(within(decision).getByText("先确认账户风险预算")).toBeInTheDocument();
});

it("renders independent index evidence without treating trend readiness as earnings coverage", async () => {
  render(<MacroContextStrip data={context} error={false} />);
  await expand();
  const market = screen.getByRole("region", { name: "当前指数证据" });
  expect(within(market).getByText("沪深300")).toBeInTheDocument();
  expect(within(market).getByText(/63期动量：3.00%/)).toBeInTheDocument();
  expect(within(market).getByText(/最大回撤：-20.00%/)).toBeInTheDocument();
  expect(within(market).getByText("尚无可核验盈利数据")).toBeInTheDocument();
  expect(within(market).getByText(/盈利与估值覆盖/)).toBeInTheDocument();
});

it("tracks research and acknowledges notices only after explicit button clicks", async () => {
  const monitor = vi.fn(),
    ack = vi.fn();
  render(
    <MacroContextStrip
      data={context}
      error={false}
      onMonitor={monitor}
      onNoticeAck={ack}
    />,
  );
  expect(monitor).not.toHaveBeenCalled();
  expect(ack).not.toHaveBeenCalled();
  const user = await expand();
  await user.click(screen.getByRole("button", { name: "跟踪研究条件" }));
  expect(monitor).toHaveBeenCalledWith(true);
  await user.click(
    screen.getByRole("button", { name: "标记研究条件发生变化为已读" }),
  );
  expect(ack).toHaveBeenCalledWith("notice-1");
});

it("never invents a critical alert from legacy data or keeps one during read failure", () => {
  const { rerender } = render(
    <MacroContextStrip data={context} error={true} />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("读取失败");
  expect(
    screen.queryByText(context.radar!.headline.impact),
  ).not.toBeInTheDocument();
  rerender(
    <MacroContextStrip
      data={{ state: "UNVERIFIED", items: [] }}
      error={false}
    />,
  );
  expect(document.querySelector(".op-critical-impact")).not.toBeInTheDocument();
});

it("hides unknown and conflicting history points and exposes daily details by keyboard", async () => {
  const rates = {
    ...context.series![0],
    history: [
      { date: "2026-10-09", value: -0.7, state: "READY" },
      { date: "2026-10-12", value: -0.6, state: "UNKNOWN" },
      { date: "2026-10-13", value: -0.5, state: "READY" },
      { date: "2026-10-14", value: -0.4, state: "CONFLICT" },
    ],
  };
  render(
    <MacroContextStrip data={{ ...context, series: [rates] }} error={false} />,
  );
  const user = await expand();
  const chart = screen.getByRole("img", {
    name: "美国10年期实际利率 历史观察",
  });
  expect(
    within(chart).queryByRole("button", { name: /2026年10月12日/ }),
  ).not.toBeInTheDocument();
  expect(chart.querySelectorAll("line")).toHaveLength(0);
  const last = within(chart).getByRole("button", { name: /2026年10月13日/ });
  last.focus();
  await user.keyboard("{Enter}");
  expect(
    screen.getByLabelText("美国10年期实际利率 历史明细"),
  ).toHaveTextContent("2026年10月13日：-0.5 %");
  expect(within(chart).queryByText("2026-10-13")).not.toBeInTheDocument();
});

it("accepts backend observed history while breaking a definition-version change", async () => {
  const rates = {
    ...context.series![0],
    history: [
      {
        date: "2026-10-12",
        value: -0.6,
        state: "OBSERVED",
        definitionKey: "UST_REAL10",
        definitionVersion: "v1",
      },
      {
        date: "2026-10-13",
        value: -0.5,
        state: "OBSERVED",
        definitionKey: "UST_REAL10",
        definitionVersion: "v2",
      },
    ],
  };
  render(
    <MacroContextStrip data={{ ...context, series: [rates] }} error={false} />,
  );
  await expand();
  const chart = screen.getByRole("img", {
    name: "美国10年期实际利率 历史观察",
  });
  expect(within(chart).getAllByRole("button")).toHaveLength(2);
  expect(chart.querySelectorAll("line")).toHaveLength(0);
});

it("does not promote a stale critical cross-market association or turn gaps into risk", async () => {
  render(
    <MacroContextStrip
      data={{
        ...context,
        series: [{ ...context.series![0], state: "STALE" }],
        radar: {
          ...context.radar!,
          headline: {
            ...context.radar!.headline,
            kind: "GAP",
            summary: "关键证据不足",
            impact: "等待新鲜证据",
          },
        },
      }}
      error={false}
      selectedDirection={directions[0]}
      directions={directions}
      onDirectionSelect={vi.fn()}
    />,
  );
  await expand();
  expect(
    screen.queryByRole("button", { name: "研究 黄金 · 黄金现货" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("关键证据不足")).toBeInTheDocument();
  expect(screen.queryByText("风险已经上升")).not.toBeInTheDocument();
});

it("displays actual portfolio ratios and keeps unknown weights empty", async () => {
  render(
    <MacroContextStrip
      data={{
        ...context,
        portfolio: {
          ...context.portfolio!,
          heldSelected: true,
          selectedWeight: 0.125,
          sameIndexWeight: 0.25,
          cashRatio: 0,
          asOf: "2026-10-14T04:00:00Z",
        },
      }}
      error={false}
    />,
  );
  await expand();
  const portfolio = screen.getByRole("region", { name: "策略与个人持仓" });
  expect(
    within(portfolio).getByText(
      /当前标的权重：12.50% · 同指数敞口：25.00% · 现金比例：0.00%/,
    ),
  ).toBeInTheDocument();
  expect(
    within(portfolio).getByText("持仓状态：已持有当前标的"),
  ).toBeInTheDocument();
});

it("uses calendar-month changes for monthly credit without relabeling them as daily samples", async () => {
  const monthly = {
    ...context.series![0],
    code: "M1_YOY",
    label: "M1同比（新口径）",
    frequency: "MONTHLY" as const,
    value: 4.5,
    asOf: "2026-09-01",
    changes: {
      ...context.series![0].changes,
      previousValue: 4.2,
      change5Samples: null,
      change20Samples: null,
      changeUnit: "pp" as const,
      periodChange: 0.3,
      change3Months: 0.5,
      change6Months: null,
    },
    history: [],
  };
  render(
    <MacroContextStrip
      data={{ ...context, series: [monthly] }}
      error={false}
    />,
  );
  await expand();
  const credit = screen.getByRole("region", { name: "关键变量证据" });
  expect(
    within(credit).getByText("连续月变化：+0.30 个百分点"),
  ).toBeInTheDocument();
  expect(
    within(credit).getByText("近3月变化：+0.50 个百分点"),
  ).toBeInTheDocument();
  expect(within(credit).getByText("近6月变化：暂不可计算")).toBeInTheDocument();
  expect(
    within(credit).queryByText(/近5个可比较观察样本/),
  ).not.toBeInTheDocument();
});

it("keeps YTD credit cumulative and does not connect observations across years", async () => {
  const ytd = {
    ...context.series![0],
    code: "CORP_MEDLONG_LOANS_YTD",
    label: "企业中长期贷款累计新增",
    frequency: "YTD" as const,
    unit: "万亿元",
    value: 5,
    history: [
      { date: "2025-12-01", value: 9, state: "READY" },
      { date: "2026-03-01", value: 5, state: "READY" },
    ],
  };
  render(
    <MacroContextStrip data={{ ...context, series: [ytd] }} error={false} />,
  );
  await expand();
  const credit = screen.getByRole("region", { name: "关键变量证据" });
  expect(
    within(credit).getByText(
      "年内累计值，未经核实不计算单月差，也不跨年比较。",
    ),
  ).toBeInTheDocument();
  expect(
    within(credit).queryByText(/近5个可比较观察样本/),
  ).not.toBeInTheDocument();
  expect(within(credit).getByRole("img").querySelectorAll("line")).toHaveLength(
    0,
  );
});

function officialYtd(metadata: OpportunitySeries["metadata"]) {
  return {
    ...context.series![0],
    code: "CORP_MEDLONG_LOANS_YTD",
    label: "企业中长期贷款累计新增",
    frequency: "YTD" as const,
    unit: "万亿元",
    value: 6.53,
    asOf: "2026-09-01",
    sourceName: "中国人民银行",
    sourceUri:
      "https://www.pbc.gov.cn/diaochatongjisi/116219/116225/2026101417073743604/index.html",
    methodVersion: "opportunity-official-v1",
    metadata,
    history: [],
    changes: {
      ...context.series![0].changes,
      previousValue: null,
      change5Samples: null,
      change20Samples: null,
      changeUnit: null,
      median: null,
      percentile: null,
      sampleCount: 1,
    },
  };
}

it("shows the official YTD accumulation interval instead of the reference month's first day", async () => {
  const ytd = officialYtd({
    definitionKey: "PBOC_CORPORATE_MEDLONG_LOANS_NET_INCREASE_YTD",
    definitionVersion: "PBOC_FINANCIAL_STATISTICS_2023",
    accumulationStart: "2026-01-01",
    accumulationEnd: "2026-09-30",
  });
  render(
    <MacroContextStrip data={{ ...context, series: [ytd] }} error={false} />,
  );
  await expand();
  const credit = screen.getByRole("region", { name: "关键变量证据" });
  expect(
    within(credit).getByText(/累计区间：2026年1月1日—2026年9月30日/),
  ).toBeInTheDocument();
  expect(
    within(credit).queryByText(/观察期：2026年9月1日/),
  ).not.toBeInTheDocument();
  expect(within(credit).getByText("6.53 万亿元")).toBeInTheDocument();
  expect(
    within(credit).getByText(
      "年内累计值，未经核实不计算单月差，也不跨年比较。",
    ),
  ).toBeInTheDocument();
});

it.each([
  undefined,
  { accumulationStart: "2026-01-01" },
  { accumulationEnd: "2026-09-30" },
  { accumulationStart: "2026-10-01", accumulationEnd: "2026-09-30" },
  { accumulationStart: "2025-01-01", accumulationEnd: "2026-09-30" },
  { accumulationStart: "2026-01-01", accumulationEnd: "2026-08-31" },
  { accumulationStart: "2026-01-01", accumulationEnd: "2026-09-31" },
  { accumulationStart: "2026-01-01", accumulationEnd: null },
])(
  "keeps missing or contradictory official YTD intervals unknown without inventing dates (%j)",
  async (metadata) => {
    render(
      <MacroContextStrip
        data={{ ...context, series: [officialYtd(metadata)] }}
        error={false}
      />,
    );
    await expand();
    const credit = screen.getByRole("region", { name: "关键变量证据" });
    expect(within(credit).getByText(/累计区间：待核查/)).toBeInTheDocument();
    expect(
      within(credit).queryByText(/观察期：2026年9月1日/),
    ).not.toBeInTheDocument();
    expect(
      within(credit).queryByText(/2026年1月1日—2026年9月30日/),
    ).not.toBeInTheDocument();
  },
);

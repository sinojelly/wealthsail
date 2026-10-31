import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  ActionableZoneResult,
  DailyResearch,
  IndustryCycle,
  IndustryRiskCheck,
  ValueDirection,
} from "../../api/investment";
import ActionablePlan from "./components/ActionablePlan";
import ValueDirections from "./components/ValueDirections";
import {
  IndustryCycleBadge,
  IndustryCycleWarning,
} from "./components/IndustryCycle";

const auditChecks = (): IndustryRiskCheck[] => [
  {
    kind: "FINANCING_COOLING",
    status: "FOUND",
    verification: "SOURCE_CHECKED",
    strength: "CONFIRMED",
    independentSourceCount: 1,
    summary: "行业融资笔数同比减少，统计范围限所列样本。",
    query: "机器人 一级市场融资降温 2026",
    sourceIds: ["official"],
  },
  {
    kind: "LAYOFFS_CLOSURES",
    status: "FOUND",
    verification: "UNVERIFIED",
    strength: "SUSPECTED",
    independentSourceCount: 1,
    summary: "单家企业欠薪消息尚待核对，不外推全行业。",
    query: "机器人 欠薪 裁员 倒闭",
    sourceIds: ["media"],
  },
  {
    kind: "ORDER_CUTS",
    status: "NOT_FOUND",
    verification: "UNVERIFIED",
    strength: "NONE",
    independentSourceCount: 0,
    summary: "本轮公开检索未检得可引用的砍单原文。",
    query: "机器人 下游砍单 订单取消",
    sourceIds: [],
  },
  {
    kind: "OVERCAPACITY_PRICE_WAR",
    status: "UNVERIFIED",
    verification: "UNAVAILABLE",
    strength: "NONE",
    independentSourceCount: 0,
    summary: "原始材料暂不可用，无法确认产能利用率。",
    query: "机器人 产能过剩 价格战",
    sourceIds: [],
  },
  {
    kind: "PRODUCTION_DELAY",
    status: "NOT_FOUND",
    verification: "UNVERIFIED",
    strength: "NONE",
    independentSourceCount: 0,
    summary: "本轮未检得新的量产推迟公告。",
    query: "机器人 商业化量产推迟",
    sourceIds: [],
  },
];

const warning = "⚠️ 行业去泡沫/等待订单期 · 谨防左侧接飞刀";
const plan = (): ActionableZoneResult => ({
  methodVersion: "actionable-zones-v3",
  subjectKind: "fund",
  subjectId: "robotics-fund",
  label: "产业研究基金",
  priceBasis: "ADJUSTED_PRICE",
  unit: "CNY",
  asOf: "2026-10-30",
  sourceUri: null,
  currentPrice: 1.15,
  indicators: { ma60: 1.3 },
  selectedTrack: "LEFT_VALUE",
  tracks: [
    {
      track: "LEFT_VALUE",
      state: "WATCH",
      entryZoneMin: 0.95,
      entryZoneMax: 1.05,
      stopLossPrice: 0.9,
      takeProfitT1: 1.25,
      takeProfitT2: 1.35,
      priceState: "BELOW_ZONE",
      reasons: [],
    },
  ],
});
const direction = (): ValueDirection => ({
  key: "robotics",
  title: "机器人与具身智能",
  thesis: "等待订单和利润验证。",
  signal: "LEFT_VALUE",
  sourceIds: [],
  macroCodes: [],
  watchFor: [],
  plans: [plan()],
});
const daily = (row: ValueDirection): DailyResearch => ({
  methodVersion: "daily-research-v1",
  state: "READY",
  runId: "run",
  reportRunId: "report",
  scheduledFor: null,
  startedAt: null,
  completedAt: null,
  asOf: "2026-10-30",
  nextRunAt: null,
  report: {
    summary: "产业研究",
    coverage: [],
    sources: [],
    events: [],
    upcoming: [],
    energy: [],
    valueDirections: [row],
  },
});

it.each([
  ["EARNINGS_DELIVERY", "🟢 业绩兑现期"],
  ["POLICY_CATALYST", "🟡 政策/事件催化期"],
  ["EARLY_COMMERCIALIZATION", "🟠 商业化早期·去伪存真期"],
] as const)(
  "shows the explicit %s stage beside the direction and full plan titles",
  (stage, label) => {
    const row = direction();
    Object.assign(row, { industryCycle: { stage } });
    const full = plan();
    Object.assign(full, { industryCycle: { stage } });
    render(
      <>
        <ValueDirections data={daily(row)} />
        <ActionablePlan result={full} />
      </>,
    );
    const value = screen.getByRole("region", { name: row.title });
    expect(within(value).getByLabelText("产业兑现阶段")).toHaveTextContent(
      label,
    );
    expect(within(value).getByText("左侧低吸观察")).toBeVisible();
    const complete = screen.getByRole("region", {
      name: "产业研究基金双轨计划",
    });
    expect(within(complete).getByLabelText("产业兑现阶段")).toHaveTextContent(
      label,
    );
  },
);

it("keeps an unclassified robotics direction pending verification instead of inferring delivery from its title", () => {
  render(<ValueDirections data={daily(direction())} />);
  expect(screen.getByLabelText("产业兑现阶段")).toHaveTextContent(
    "产业阶段待核查",
  );
  expect(screen.queryByText("🟢 业绩兑现期")).not.toBeInTheDocument();
  expect(
    screen.queryByText("🟠 商业化早期·去伪存真期"),
  ).not.toBeInTheDocument();
});

it("carries an early-commercialization warning into compact and full plan status without changing server price zones", () => {
  const row = direction();
  Object.assign(row, {
    industryCycle: {
      stage: "EARLY_COMMERCIALIZATION",
      evidenceSummary: "融资降温，尚无当期利润与回款验证。",
    },
    industryRiskWarning: warning,
  });
  const full = plan();
  Object.assign(full, {
    industryCycle: row.industryCycle,
    industryRiskWarning: warning,
  });
  const original = JSON.stringify({ row, full });
  render(
    <>
      <ValueDirections data={daily(row)} />
      <ActionablePlan result={full} />
    </>,
  );
  const compact = screen.getByRole("article", { name: "产业研究基金买卖计划" });
  const complete = screen.getByRole("region", { name: "产业研究基金双轨计划" });
  for (const card of [compact, complete]) {
    const status = within(card).getByRole("status", { name: "产业周期风险" });
    expect(status).toBeVisible();
    expect(status).toHaveTextContent(warning);
    expect(status).toHaveTextContent("仅适合极小仓位观察或等待右侧拐点");
    expect(card).toHaveTextContent("0.950–1.050");
  }
  expect(JSON.stringify({ row, full })).toBe(original);
});

it("shows supplied relative strength and the MA60 breakdown date as analysis evidence", async () => {
  const result = plan();
  Object.assign(result, {
    relativeStrength: {
      excessReturn20d: -5.25,
      excessReturn60d: -12.1,
      drawdown60d: 18.5,
      breakdownDate: "2026-09-18",
      weakening: true,
    },
  });
  render(<ActionablePlan result={result} />);
  await userEvent.click(screen.getByText("价格依据与算法"));
  const evidence = screen.getByRole("region", { name: "板块相对强弱依据" });
  expect(evidence).toBeVisible();
  expect(evidence).toHaveTextContent("20日超额收益：-5.25 个百分点");
  expect(evidence).toHaveTextContent("60日超额收益：-12.10 个百分点");
  expect(evidence).toHaveTextContent("近60日高点回落：18.50%");
  expect(evidence).toHaveTextContent(
    "本轮走弱分水岭（跌破MA60）：2026年9月18日",
  );
  expect(evidence).toHaveTextContent(
    "📉 相对沪深300持续走弱（已触发趋势风控）",
  );
});

it("leaves incomplete relative strength values unknown instead of displaying zero or inventing a breakdown date", async () => {
  const result = plan();
  Object.assign(result, {
    relativeStrength: {
      excessReturn20d: 0,
      excessReturn60d: null,
      drawdown60d: null,
      breakdownDate: null,
    },
  });
  render(<ActionablePlan result={result} />);
  await userEvent.click(screen.getByText("价格依据与算法"));
  const evidence = screen.getByRole("region", { name: "板块相对强弱依据" });
  expect(evidence).toHaveTextContent("20日超额收益：0.00 个百分点");
  expect(evidence).toHaveTextContent("60日超额收益：—");
  expect(evidence).toHaveTextContent("近60日高点回落：—");
  expect(evidence).toHaveTextContent("本轮走弱分水岭（跌破MA60）：尚未确认");
});

it("lets readers inspect all five counter-evidence checks without requiring a warning or treating an empty search as safety", async () => {
  const cycle: IndustryCycle = {
    stage: "POLICY_CATALYST",
    riskLevel: "SUSPECTED",
    riskChecks: auditChecks(),
    evidenceGaps: ["CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING"],
  };
  render(<IndustryCycleWarning cycle={cycle} />);
  const summary = screen.getByText("查看5项产业反方核查与证据明细");
  expect(summary.closest("details")).not.toHaveAttribute("open");
  await userEvent.click(summary);
  const financing = screen.getByRole("region", { name: "融资降温核查" });
  expect(financing).toBeVisible();
  expect(financing).toHaveTextContent("发现风险");
  expect(financing).toHaveTextContent("已核对原文");
  expect(financing).toHaveTextContent("独立来源：1");
  expect(financing).toHaveTextContent("行业融资笔数同比减少");
  expect(financing).toHaveTextContent("机器人 一级市场融资降温 2026");
  const layoffs = screen.getByRole("region", { name: "欠薪倒闭核查" });
  expect(layoffs).toHaveTextContent("媒体线索");
  expect(layoffs).toHaveTextContent("待核实");
  const orders = screen.getByRole("region", { name: "下游砍单核查" });
  expect(orders).toHaveTextContent("本轮未检得显著负面");
  expect(orders).toHaveTextContent("不等于不存在风险");
  expect(
    screen.getByRole("region", { name: "产能价格战核查" }),
  ).toHaveTextContent("待核验");
  expect(screen.getByRole("region", { name: "量产推迟核查" })).toBeVisible();
  expect(
    screen.getByRole("region", { name: "产业证据缺口" }),
  ).toHaveTextContent("当期利润增长或真实回款证据不足");
  expect(
    screen.queryByText(/已确认没有风险|行业没有风险/),
  ).not.toBeInTheDocument();
});

it("keeps a gaps-only audit available and labels missing check records honestly", async () => {
  render(
    <IndustryCycleWarning
      cycle={{
        stage: "UNKNOWN",
        evidenceGaps: ["CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING"],
      }}
    />,
  );
  await userEvent.click(screen.getByText("查看5项产业反方核查与证据明细"));
  expect(screen.getAllByText("未提供核查记录")).toHaveLength(5);
  expect(screen.getByRole("region", { name: "产业证据缺口" })).toBeVisible();
});

it("shows server warning wording before cycle or legacy warning text while keeping stage and risk strength separate", () => {
  const cycle: IndustryCycle = {
    stage: "EARNINGS_DELIVERY",
    riskLevel: "SUSPECTED",
    industryRiskWarning: "⚠️ 存在待核实的行业降温线索",
  };
  render(
    <>
      <IndustryCycleBadge cycle={cycle} />
      <IndustryCycleWarning
        cycle={cycle}
        warning="⚠️ 单一企业融资线索待核实，尚不足以外推全行业"
      />
    </>,
  );
  expect(screen.getByLabelText("产业兑现阶段")).toHaveTextContent(
    "🟢 业绩兑现期",
  );
  expect(screen.getByLabelText("行业风险强度")).toHaveTextContent("待核实");
  expect(
    screen.getByRole("status", { name: "产业周期风险" }),
  ).toHaveTextContent("单一企业融资线索待核实");
  expect(
    screen.queryByText("⚠️ 存在待核实的行业降温线索"),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(warning)).not.toBeInTheDocument();
});

it("uses the cycle's server warning when no plan warning is available", () => {
  render(
    <IndustryCycleWarning
      cycle={{
        stage: "UNKNOWN",
        riskLevel: "UNPROVEN",
        industryRiskWarning: "⚠️ 仅见远期扩产/框架意向，尚无当期盈利兑现",
      }}
    />,
  );
  expect(
    screen.getByRole("status", { name: "产业周期风险" }),
  ).toHaveTextContent("仅见远期扩产/框架意向");
});

it("limits the NONE risk label to this round's unconfirmed negative findings", () => {
  render(
    <IndustryCycleBadge
      cycle={{ stage: "POLICY_CATALYST", riskLevel: "NONE" }}
    />,
  );
  expect(screen.getByLabelText("行业风险强度")).toHaveTextContent(
    /^行业风险：本轮未确认负面$/,
  );
  expect(screen.queryByText(/无风险|没有风险/)).not.toBeInTheDocument();
});

it.each([
  [false, true],
  [true, false],
  [undefined, false],
] as const)(
  "discloses model-only search provenance only when execution verification is explicitly %s",
  async (verified, disclosed) => {
    const cycle = Object.assign(
      { stage: "UNKNOWN" as const, riskChecks: auditChecks() },
      { searchExecutionVerified: verified },
    );
    render(<IndustryCycleWarning cycle={cycle} />);
    const noteText = "检索凭证：模型结构化核查（外部搜索回执待接入）";
    const summary = screen.getByText("查看5项产业反方核查与证据明细");
    if (disclosed) expect(screen.getByText(noteText)).not.toBeVisible();
    else expect(screen.queryByText(noteText)).not.toBeInTheDocument();
    await userEvent.click(summary);
    if (disclosed) expect(screen.getByText(noteText)).toBeVisible();
    else expect(screen.queryByText(noteText)).not.toBeInTheDocument();
  },
);

it("distinguishes lower-high trend pressure from an unconfirmed MA60 crossing", async () => {
  const result = plan();
  Object.assign(result, {
    relativeStrength: {
      excessReturn20d: -5,
      excessReturn60d: -10,
      drawdown60d: 15,
      breakdownDate: null,
      weakening: false,
      lowerHighs: true,
      trendWeakening: true,
    },
  });
  render(<ActionablePlan result={result} />);
  await userEvent.click(screen.getByText("价格依据与算法"));
  const evidence = screen.getByRole("region", { name: "板块相对强弱依据" });
  expect(evidence).toHaveTextContent("反弹高点下移");
  expect(evidence).toHaveTextContent("本轮走弱分水岭（跌破MA60）：尚未确认");
  expect(evidence).not.toHaveTextContent(
    "相对沪深300持续走弱（已触发趋势风控）",
  );
});

it("connects the compact card's inherited audit to report source links without changing the supplied zones", async () => {
  const row = direction();
  row.industryCycle = {
    stage: "POLICY_CATALYST",
    riskLevel: "SUSPECTED",
    riskChecks: auditChecks(),
  };
  const data = daily(row);
  data.report!.sources = [
    {
      id: "official",
      title: "行业融资原始统计",
      url: "https://example.org/industry",
      quote: "行业融资笔数减少，适用统计口径以原文为准。",
      publishedDate: "2026-10-30",
      publishedAt: null,
      retrievedAt: "2026-10-30T08:00:00+08:00",
      verification: "SOURCE_CHECKED",
    },
    {
      id: "media",
      title: "无效链接",
      url: "javascript:alert(1)",
      quote: "未核实企业线索。",
      publishedDate: "2026-10-30",
      publishedAt: null,
      retrievedAt: "2026-10-30T08:00:00+08:00",
      verification: "UNVERIFIED",
    },
  ];
  const original = JSON.stringify(data);
  render(<ValueDirections data={data} />);
  const compact = screen.getByRole("article", { name: "产业研究基金买卖计划" });
  await userEvent.click(
    within(compact).getByText("查看5项产业反方核查与证据明细"),
  );
  expect(
    within(compact).getByRole("link", { name: "[行业融资原始统计]" }),
  ).toHaveAttribute("href", "https://example.org/industry");
  expect(compact.querySelector('[href^="javascript:"]')).toBeNull();
  expect(compact).toHaveTextContent("0.950–1.050");
  expect(JSON.stringify(data)).toBe(original);
});

it("shows omitted industry candidates before the direction cards and highlights omitted historical risk sectors", () => {
  const data = daily(direction());
  Object.assign(data.report!, {
    industryResearchCoverage: {
      maximumCandidates: 10,
      candidateCount: 10,
      omittedCandidateCount: 7,
      omittedRiskCandidateCount: 2,
      status: "PARTIAL",
    },
  });
  const original = JSON.stringify(data);
  render(<ValueDirections data={data} />);
  const coverage = screen.getByRole("status", { name: "行业反方核查覆盖缺口" });
  expect(coverage).toBeVisible();
  expect(coverage).toHaveTextContent(
    "本轮行业反方核查覆盖10个候选，另有7个候选未纳入；检索覆盖存在缺口",
  );
  expect(coverage).toHaveTextContent("含2个历史风险赛道");
  const card = screen.getByRole("region", { name: "机器人与具身智能" });
  expect(
    coverage.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(JSON.stringify(data)).toBe(original);
});

it("uses the server's actual covered count and leaves an unavailable omitted-risk count unspecified", () => {
  const data = daily(direction());
  Object.assign(data.report!, {
    industryResearchCoverage: {
      maximumCandidates: 10,
      candidateCount: 8,
      omittedCandidateCount: 3,
      status: "PARTIAL",
    },
  });
  render(<ValueDirections data={data} />);
  const coverage = screen.getByRole("status", { name: "行业反方核查覆盖缺口" });
  expect(coverage).toHaveTextContent(
    "本轮行业反方核查覆盖8个候选，另有3个候选未纳入",
  );
  expect(coverage).not.toHaveTextContent("历史风险赛道");
});

it.each([
  undefined,
  {
    maximumCandidates: 10,
    candidateCount: 10,
    omittedCandidateCount: 0,
    omittedRiskCandidateCount: 0,
    status: "COMPLETE",
  },
])(
  "does not turn legacy or zero-omission coverage into a search receipt: %j",
  (coverage) => {
    const row = direction();
    row.industryCycle = {
      stage: "UNKNOWN",
      riskChecks: auditChecks(),
      searchExecutionVerified: false,
    };
    const data = daily(row);
    if (coverage)
      Object.assign(data.report!, { industryResearchCoverage: coverage });
    render(<ValueDirections data={data} />);
    expect(
      screen.queryByRole("status", { name: "行业反方核查覆盖缺口" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/已完成外部搜索|外部搜索已核验/),
    ).not.toBeInTheDocument();
    expect(
      screen.getAllByText("检索凭证：模型结构化核查（外部搜索回执待接入）"),
    ).toHaveLength(2);
  },
);

it.each([
  [true, "甲公司", "2026Q3", true],
  [false, "甲公司", "2026Q3", false],
  [true, undefined, "2026Q3", false],
  [true, "甲公司", undefined, false],
  [true, "", "2026Q3", false],
  [true, "甲公司", "", false],
] as const)(
  "shows a company/period evidence identity only for a verified complete pair: %s/%s/%s",
  async (verified, entity, period, displayed) => {
    const cycle = Object.assign(
      {
        stage: "UNKNOWN" as const,
        earningsVerified: verified,
        riskChecks: auditChecks(),
      },
      { earningsEntity: entity, earningsReportPeriod: period },
    );
    render(<IndustryCycleWarning cycle={cycle} />);
    await userEvent.click(screen.getByText("查看5项产业反方核查与证据明细"));
    if (displayed)
      expect(screen.getByText("公司级证据：甲公司 · 2026Q3")).toBeVisible();
    else expect(screen.queryByText(/公司级证据：/)).not.toBeInTheDocument();
  },
);

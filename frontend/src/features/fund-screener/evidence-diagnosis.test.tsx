import type { ReactNode } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ResearchDiagnosis from "./components/ResearchDiagnosis";
import FundResultsTable from "./components/FundResultsTable";
import type { Analysis, ResearchFund } from "../../api/investment";

// Capture the chart's public data prop; jsdom cannot measure chart layouts.
vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  LineChart: ({
    data,
    children,
  }: {
    data: Array<{ date: string }>;
    children: ReactNode;
  }) => (
    <div
      role="img"
      aria-label="历史数据图"
      data-sample-count={data.length}
      data-first-date={data[0]?.date}
    >
      {children}
    </div>
  ),
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
}));

const analysis: Analysis = {
  state: "STRONG",
  score: 80,
  indicators: {},
  reasons: [],
  seriesKind: "PROVIDER_RETURN_INDEX",
  points: Array.from({ length: 1000 }, (_, i) => ({
    date: new Date(Date.UTC(2024, 0, 1 + i)).toISOString().slice(0, 10),
    close: 100 + i,
  })),
  performance: {
    state: "READY",
    eligible: true,
    return63: 0.1,
    return252: 0.2,
    maxDrawdown: 0.15,
    volatility20: 0.18,
    historyStart: "2024-01-01",
    historyEnd: "2026-09-26",
    sampleCount: 1000,
    reasons: [],
  },
  valueEvidence: {
    eligible: true,
    state: "RESEARCH_CANDIDATE",
    reasons: ["估值、费用、历史风险及映射证据满足门槛，仅列为研究候选。"],
    evidence: {
      pe: { state: "READY", latest: 12, percentile: 20, sampleCount: 900 },
      pb: { state: "READY", latest: 1.2, percentile: 25, sampleCount: 900 },
      costs: {
        state: "READY",
        managementRate: 0.002,
        custodyRate: 0.001,
        salesServiceRate: 0,
        totalRate: 0.003,
        sourceUri: "https://fund.example.org/fees",
      },
      performance: { state: "READY", eligible: true, maxDrawdown: 0.15 },
      limitations: ["用户组合适配尚未评估。"],
    },
  },
  costs: {
    state: "READY",
    managementRate: 0.002,
    custodyRate: 0.001,
    salesServiceRate: 0,
    totalRate: 0.003,
    sourceUri: "https://fund.example.org/fees",
    retrievedAt: "2026-10-13T23:00:00+08:00",
  },
};

function diagnosis(data: Analysis) {
  return (
    <ResearchDiagnosis
      title="测试基金"
      code="110022"
      mode="fund"
      path="A股 / 宽基"
      data={data}
      loading={false}
      error={false}
      onRefresh={() => undefined}
      refreshing={false}
      canRefresh={true}
      message=""
      watchButton={null}
    />
  );
}

it("shows independent historical risk and research eligibility without claiming investment value", () => {
  render(diagnosis(analysis));
  const risk = screen.getByRole("region", { name: "历史收益与风险" });
  expect(within(risk).getByText("10.00%")).toBeInTheDocument();
  expect(within(risk).getByText("20.00%")).toBeInTheDocument();
  expect(within(risk).getByText("15.00%")).toBeInTheDocument();
  expect(within(risk).getByText("18.00%")).toBeInTheDocument();
  expect(
    within(risk).getByText(/2024-01-01 至 2026-09-26/),
  ).toBeInTheDocument();
  expect(within(risk).getByText(/1000 个实际观察值/)).toBeInTheDocument();
  expect(screen.getByText("研究资格：满足研究门槛")).toBeInTheDocument();
  expect(
    screen.getByText(/仅作为研究候选，尚未证实长期配置价值/),
  ).toBeInTheDocument();
  expect(screen.getByText("PE 分位：20.00%")).toBeInTheDocument();
  expect(screen.getByText("PB 分位：25.00%")).toBeInTheDocument();
});

it("keeps unknown metrics empty and explains incomplete eligibility", () => {
  render(
    diagnosis({
      ...analysis,
      performance: {
        state: "UNSUPPORTED",
        eligible: false,
        return63: null,
        return252: null,
        maxDrawdown: null,
        volatility20: null,
        historyStart: "2026-01-01",
        historyEnd: "2026-09-01",
        sampleCount: 20,
        reasons: ["单位净值不等同于总收益。"],
      },
      valueEvidence: {
        ...analysis.valueEvidence!,
        eligible: false,
        state: "INCOMPLETE",
        reasons: ["跟踪指数映射未核验。"],
      },
    }),
  );
  const risk = screen.getByRole("region", { name: "历史收益与风险" });
  expect(within(risk).getAllByText("—")).toHaveLength(4);
  expect(
    within(risk).getByText("单位净值不等同于总收益。"),
  ).toBeInTheDocument();
  expect(screen.getByText("研究资格：证据不足")).toBeInTheDocument();
  expect(screen.getByText("跟踪指数映射未核验。")).toBeInTheDocument();
});

it("passes 253, 756 or all actual observations to the history chart", async () => {
  const user = userEvent.setup();
  render(diagnosis(analysis));
  await user.click(screen.getByRole("tab", { name: "技术趋势" }));
  expect(screen.getByText(/供应商分红再投收益指数/)).toBeInTheDocument();
  const window = screen.getByRole("combobox", { name: "历史走势窗口" });
  expect(screen.getByRole("img", { name: "历史数据图" })).toHaveAttribute(
    "data-sample-count",
    "253",
  );
  await user.selectOptions(window, "3y");
  expect(screen.getByRole("img", { name: "历史数据图" })).toHaveAttribute(
    "data-sample-count",
    "756",
  );
  await user.selectOptions(window, "all");
  expect(screen.getByRole("img", { name: "历史数据图" })).toHaveAttribute(
    "data-sample-count",
    "1000",
  );
  expect(screen.getByRole("img", { name: "历史数据图" })).toHaveAttribute(
    "data-first-date",
    "2024-01-01",
  );
});

it.each([
  ["UNIT_NAV", "单位净值，分红日可能降低，不能直接视为总收益"],
  ["ACCUMULATED_NAV", "累计净值，不代表分红再投资总回报"],
  ["ETF_HFQ", "ETF 后复权价格，采用供应商分红与拆分调整口径"],
  ["INDEX_PRICE", "指数价格，不包含分红再投总收益"],
])("identifies the %s history basis accurately", async (seriesKind, label) => {
  const user = userEvent.setup();
  render(diagnosis({ ...analysis, seriesKind }));
  await user.click(screen.getByRole("tab", { name: "技术趋势" }));
  expect(screen.getByText(new RegExp(label))).toBeInTheDocument();
});

it("shows disclosed operating costs and their provenance separately from missing product quality", async () => {
  const user = userEvent.setup();
  render(diagnosis(analysis));
  await user.click(screen.getByRole("tab", { name: "产品质量" }));
  expect(screen.getByText("管理费：0.20%")).toBeInTheDocument();
  expect(screen.getByText("托管费：0.10%")).toBeInTheDocument();
  expect(screen.getByText("销售服务费：0.00%")).toBeInTheDocument();
  expect(screen.getByText("年运作费用合计：0.30%")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "费用披露来源" })).toHaveAttribute(
    "href",
    "https://fund.example.org/fees",
  );
  expect(screen.getByText(/2026-10-13T23:00:00\+08:00/)).toBeInTheDocument();
  expect(
    screen.getByText(/跟踪误差、流动性、折溢价与组合适配尚未评估/),
  ).toBeInTheDocument();
});

it("shows qualification and risk inside an expanded row without increasing table columns", async () => {
  const user = userEvent.setup();
  const fund: ResearchFund = {
    instrumentId: "fund-id",
    fundCode: "110022",
    fundName: "测试基金",
    fundType: "指数",
    tradeForm: "FUND",
    market: "FUND_CN",
    currency: "CNY",
    latestValue: null,
    latestDate: null,
    navRangePosition: null,
    technicalScore: null,
    dataState: "READY",
    updatedAt: null,
    performanceState: "READY",
    valueState: "RESEARCH_CANDIDATE",
    valueEligible: true,
    return252: 0.2,
    maxDrawdown: 0.15,
  };
  render(
    <FundResultsTable
      data={{ items: [fund], total: 1, page: 1, pageSize: 20 }}
      selectedCode={null}
      onSelect={() => undefined}
      page={1}
      onPage={() => undefined}
      loading={false}
      error={false}
    />,
  );
  await user.click(screen.getByRole("button", { name: /展开更多列/ }));
  expect(screen.getByText(/研究资格：满足研究门槛/)).toBeInTheDocument();
  expect(screen.getByText(/252 观察日收益：20.00%/)).toBeInTheDocument();
  expect(screen.getByText(/样本最大回撤：15.00%/)).toBeInTheDocument();
  expect(screen.getAllByRole("columnheader")).toHaveLength(8);
});

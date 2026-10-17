import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SeriesCard } from "./components/EvidenceCards";
import type {
  OpportunityContext,
  OpportunitySeries,
} from "../../api/investment";
const context: OpportunityContext = {
  state: "PARTIAL",
  items: [],
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
};
function SeriesFixture({
  data,
}: {
  data: OpportunityContext;
  error?: boolean;
}) {
  return (
    <section aria-label="关键变量证据">
      {data.series?.map((series) => (
        <SeriesCard key={series.code} series={series} />
      ))}
    </section>
  );
}
async function expand() {
  return userEvent.setup();
}
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

it("keeps basis-point sample changes, negative yields and restricted sources honest", async () => {
  render(<SeriesFixture data={context} error={false} />);
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
    <SeriesFixture data={{ ...context, series: [rates] }} error={false} />,
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
  act(() => last.focus());
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
    <SeriesFixture data={{ ...context, series: [rates] }} error={false} />,
  );
  await expand();
  const chart = screen.getByRole("img", {
    name: "美国10年期实际利率 历史观察",
  });
  expect(within(chart).getAllByRole("button")).toHaveLength(2);
  expect(chart.querySelectorAll("line")).toHaveLength(0);
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
    <SeriesFixture data={{ ...context, series: [monthly] }} error={false} />,
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
  render(<SeriesFixture data={{ ...context, series: [ytd] }} error={false} />);
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

it("shows the official YTD accumulation interval instead of the reference month's first day", async () => {
  const ytd = officialYtd({
    definitionKey: "PBOC_CORPORATE_MEDLONG_LOANS_NET_INCREASE_YTD",
    definitionVersion: "PBOC_FINANCIAL_STATISTICS_2023",
    accumulationStart: "2026-01-01",
    accumulationEnd: "2026-09-30",
  });
  render(<SeriesFixture data={{ ...context, series: [ytd] }} error={false} />);
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
      <SeriesFixture
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

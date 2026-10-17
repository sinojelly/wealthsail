import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ResearchFund } from "../../api/investment";
import FundResultsTable from "./components/FundResultsTable";

const fund: ResearchFund = {
  instrumentId: "fund-id",
  fundCode: "510330",
  fundName: "沪深300ETF",
  fundType: "ETF",
  tradeForm: "ETF",
  market: "SSE",
  currency: "CNY",
  latestValue: 2.5,
  latestDate: "2026-10-13",
  latestValueBasis: "ADJUSTED_PRICE",
  navRangePosition: null,
  technicalScore: null,
  dataState: "READY",
  updatedAt: null,
};

it.each([
  ["ADJUSTED_PRICE", "后复权"],
  ["UNIT_NAV", "单位净值"],
  ["ACCUMULATED_NAV", "累计净值"],
] as const)(
  "labels displayed %s values without calling them market quotes",
  async (basis, label) => {
    render(
      <FundResultsTable
        data={{
          items: [{ ...fund, latestValueBasis: basis }],
          total: 1,
          page: 1,
          pageSize: 20,
        }}
        selectedCode={null}
        onSelect={() => undefined}
        page={1}
        onPage={() => undefined}
        loading={false}
        error={false}
      />,
    );
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: /展开更多列/ }));
    expect(screen.getByText("2.5000")).toBeInTheDocument();
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText("2026-10-13")).toBeInTheDocument();
  },
);

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

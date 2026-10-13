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

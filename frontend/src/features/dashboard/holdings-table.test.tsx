import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { positionsFixture } from "../../test/fixtures";
import { renderWithSession } from "../../test/render-with-session";
import HoldingsTable from "./components/HoldingsTable";

it("accepts all visible estimated holding starts with one explicit action", async () => {
  const user = userEvent.setup();
  const confirmAll = vi.fn().mockResolvedValue(undefined);
  const estimated = {
    ...positionsFixture.at(-1)!,
    holdingPeriod: {
      ...positionsFixture.at(-1)!.holdingPeriod!,
      startedOn: "2021-01-26",
      method: "PORTFOLIO_ACTIVITY_ESTIMATE" as const,
      confidence: "LOW" as const,
    },
    rangeMetrics: {
      ...positionsFixture.at(-1)!.rangeMetrics!,
      status: "PARTIAL" as const,
      qualityReasons: ["PARTIAL_HOLDING_HISTORY"],
    },
  };

  renderWithSession(
    <HoldingsTable
      rows={[estimated]}
      onConfirmEstimatedHoldingStarts={confirmAll}
    />,
  );

  expect(
    screen.getByText(/认可系统推荐日期，可一次确认并消除提示/),
  ).toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "确认全部估算日期（1）" }),
  );
  expect(confirmAll).toHaveBeenCalledWith([
    {
      accountId: estimated.accountId,
      instrumentId: estimated.instrumentId,
      startedOn: "2021-01-26",
    },
  ]);
});

it("shows a projection gap as automatic valuation syncing instead of missing data", () => {
  const syncing = {
    ...positionsFixture.at(-1)!,
    price: null,
    marketValue: null,
    weight: null,
    pnl: null,
    pnlRate: null,
    quality: "MISSING" as const,
    valuationStatus: "SYNCING" as const,
  };

  renderWithSession(<HoldingsTable rows={[syncing]} />);

  expect(screen.getByText("估值同步中")).toBeInTheDocument();
  expect(screen.queryByText("缺失")).not.toBeInTheDocument();
});

it("uses China-market gain and loss colors and emphasizes the return rate", () => {
  const base = positionsFixture[0];
  const gain = {
    ...base,
    instrumentId: "instrument-gain",
    symbol: "000001",
    name: "正收益持仓",
    pnl: "123.00",
    pnlRate: "4.50",
  };
  const loss = {
    ...base,
    instrumentId: "instrument-loss",
    symbol: "000002",
    name: "负收益持仓",
    pnl: "-200.00",
    pnlRate: "-6.25",
  };
  const flat = {
    ...base,
    instrumentId: "instrument-flat",
    symbol: "000003",
    name: "零收益持仓",
    pnl: "0.00",
    pnlRate: "0.00",
  };

  renderWithSession(<HoldingsTable rows={[gain, loss, flat]} />);

  expect(screen.getByText("+¥123.00").closest("td")).toHaveClass(
    "holding-pnl",
    "pnl-gain",
  );
  expect(screen.getByText("-¥200.00").closest("td")).toHaveClass(
    "holding-pnl",
    "pnl-loss",
  );
  expect(screen.getByText("+¥0.00").closest("td")).toHaveClass(
    "holding-pnl",
    "pnl-flat",
  );
  expect(screen.getByText("4.50%")).toHaveClass("holding-pnl-rate");
  expect(screen.getByText("-6.25%")).toHaveClass("holding-pnl-rate");
  expect(screen.getByText("0.00%")).toHaveClass("holding-pnl-rate");
});

it("shows an auditable intraday fund estimate without replacing official NAV", () => {
  const fund = {
    ...positionsFixture.at(-1)!,
    price: "1.00",
    intradayEstimate: {
      value: "1.05",
      changePct: "5.00",
      lower: "1.03",
      upper: "1.07",
      coveragePct: "68.50",
      disclosedOn: "2026-06-30",
      confirmedNav: "1.00",
      confirmedNavAsOf: "2026-08-28T00:00:00+00:00",
      asOf: "2026-08-31T06:30:00+00:00",
      quality: "ESTIMATED" as const,
      methodVersion: "weighted-disclosed-holdings/v1",
      explanation: "按已披露成分估算",
    },
  };

  renderWithSession(<HoldingsTable rows={[fund]} />);

  expect(screen.getByText("盘中估值 1.05")).toBeInTheDocument();
  expect(screen.getByText("估算涨跌 +5.00%")).toBeInTheDocument();
  expect(screen.getByText("区间 1.03–1.07")).toBeInTheDocument();
  expect(screen.getByText("成分覆盖 68.50%")).toBeInTheDocument();
  expect(screen.getByText("披露 2026-06-30")).toBeInTheDocument();
  expect(screen.getByText(/仅供盘中跟踪，不是官方净值/)).toBeInTheDocument();
});

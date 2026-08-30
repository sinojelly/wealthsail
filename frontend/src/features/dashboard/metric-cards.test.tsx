import { screen } from "@testing-library/react";
import type { PortfolioSummary } from "../../api/types";
import { summaryFixture } from "../../test/fixtures";
import { renderWithSession } from "../../test/render-with-session";
import MetricCards from "./components/MetricCards";

it("shows holding PnL and cash-flow-aware cumulative PnL as distinct metrics", () => {
  const summary: PortfolioSummary = {
    ...summaryFixture,
    cumulativePnl: "87888.32",
    cumulativePnlRate: "0.00",
    holdingPnl: "-31584.95",
    holdingPnlRate: "-26.44",
    performanceBaselineAsOf: "2026-08-01",
    performanceBaselineMixed: false,
  };

  renderWithSession(<MetricCards summary={summary} />);

  expect(screen.getByText("持仓收益")).toBeInTheDocument();
  expect(screen.getByText("-¥31,584.95")).toHaveClass("pnl-loss");
  expect(screen.getByText("-26.44%")).toBeInTheDocument();
  expect(screen.getByText("累计收益")).toBeInTheDocument();
  expect(screen.getByText("+¥87,888.32")).toHaveClass("pnl-gain");
  expect(screen.getByText("自 2026-08-01 跟踪")).toBeInTheDocument();
});

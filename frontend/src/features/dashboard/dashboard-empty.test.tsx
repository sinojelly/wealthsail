import { screen } from "@testing-library/react";
import DashboardPage from "./DashboardPage";
import { renderWithSession } from "../../test/render-with-session";

vi.mock("../../api/hooks", () => ({
  usePortfolioSummary: () => ({
    data: {
      totalAsset: null,
      knownValue: "0.00",
      valuationCoveragePct: "0.00",
      unpricedPositionCount: 0,
      currency: "CNY",
      cashBalance: "0.00",
      positionsMarketValue: null,
      dailyPnl: null,
      dailyPnlRate: null,
      cumulativePnl: null,
      cumulativePnlRate: null,
      allocation: [],
      qualityStatus: "MISSING",
      asOf: null,
      positionCount: 0,
    },
    isLoading: false,
    isError: false,
    error: null,
  }),
  usePositions: () => ({
    data: [],
    isLoading: false,
    isError: false,
    error: null,
  }),
  useNavTrend: () => ({
    data: {
      points: [],
      benchmark: null,
      currency: "CNY",
      timezone: "Asia/Shanghai",
    },
    isLoading: false,
    isError: false,
    error: null,
  }),
  useMarketSentiment: () => ({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useAiAdvice: () => ({
    data: { items: [], disclaimer: "", generatedAt: null, model: null },
    isLoading: false,
    isError: false,
    error: null,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useInstrumentSearch: () => ({
    data: [],
    isLoading: false,
    isError: false,
    error: null,
  }),
  useAddPosition: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useConfirmPositionImport: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

it("presents an onboarding empty state instead of a valuation failure", () => {
  renderWithSession(<DashboardPage />);

  expect(
    screen.getByRole("heading", { name: "尚未录入资产" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText("完成首笔期初持仓导入后，这里会展示资产汇总和净值走势。"),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "导入首笔持仓" })).toHaveAttribute(
    "href",
    "/onboarding",
  );
  expect(screen.queryByText("总资产暂不发布")).not.toBeInTheDocument();
  expect(screen.queryByText(/仍有 0 个持仓缺少可靠估值/)).not.toBeInTheDocument();
});

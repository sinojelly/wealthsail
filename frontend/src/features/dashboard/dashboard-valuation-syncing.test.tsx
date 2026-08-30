import { screen } from "@testing-library/react";
import DashboardPage from "./DashboardPage";
import { renderWithSession } from "../../test/render-with-session";
import { meFixture } from "../../test/fixtures";

vi.mock("../../api/hooks", () => ({
  useMe: () => ({ data: meFixture, isLoading: false, error: null }),
  usePortfolioSummary: () => ({
    data: {
      totalAsset: null,
      knownValue: "0.00",
      valuationCoveragePct: "0.00",
      unpricedPositionCount: 12,
      valuationStatus: "SYNCING",
      valuationPendingCount: 12,
      currency: "CNY",
      cashBalance: "0.00",
      positionsMarketValue: null,
      dailyPnl: null,
      dailyPnlRate: null,
      holdingPnl: null,
      holdingPnlRate: null,
      cumulativePnl: null,
      cumulativePnlRate: null,
      performanceBaselineAsOf: null,
      performanceBaselineMixed: false,
      allocation: [],
      qualityStatus: "MISSING",
      asOf: "2026-08-30",
      positionCount: 12,
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
  useConfirmTiantianHoldingStart: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useConfirmTiantianHoldingStartEstimates: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
}));

it("tells ordinary users that valuation will finish automatically", () => {
  renderWithSession(<DashboardPage />);

  expect(screen.getByText("估值正在自动更新")).toBeInTheDocument();
  expect(screen.getByText(/12 个持仓的官方净值已经获取/)).toBeInTheDocument();
  expect(screen.getByText(/预计 1 分钟内完成/)).toBeInTheDocument();
  expect(screen.getByText(/无需操作/)).toBeInTheDocument();
  expect(screen.queryByText("总资产暂不发布")).not.toBeInTheDocument();
});

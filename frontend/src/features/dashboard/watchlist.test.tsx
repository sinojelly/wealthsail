import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DashboardPage from "./DashboardPage";
import { renderWithSession } from "../../test/render-with-session";
import {
  meFixture,
  navTrendFixture,
  positionsFixture,
  summaryFixture,
} from "../../test/fixtures";

const watchlistMocks = vi.hoisted(() => ({
  add: vi.fn(),
  remove: vi.fn(),
  search: vi.fn(),
  list: [] as Array<{
    symbol: string;
    assetName: string;
    assetType: "STOCK" | "ETF" | "FUND";
    addedAt: string;
  }>,
  error: null as Error | null,
  emptyPortfolio: false,
}));

vi.mock("../../api/hooks", () => ({
  usePortfolioSummary: () => ({ data: watchlistMocks.emptyPortfolio ? { ...summaryFixture, qualityStatus: "MISSING", positionCount: 0, asOf: null } : summaryFixture, isLoading: false, error: null }),
  usePositions: () => ({ data: watchlistMocks.emptyPortfolio ? [] : positionsFixture, isLoading: false, error: null }),
  useNavTrend: () => ({ data: navTrendFixture, isLoading: false, error: null }),
  useMe: () => ({ data: meFixture, isLoading: false, error: null }),
  useMarketSentiment: () => ({ data: null, isLoading: false, error: null }),
  useAiAdvice: () => ({ data: { items: [], disclaimer: "", generatedAt: null, model: null }, isLoading: false, isFetching: false, error: null, refetch: vi.fn() }),
  useInstrumentSearch: () => ({ data: watchlistMocks.search(), isLoading: false, error: null }),
  useWatchlist: () => ({ data: watchlistMocks.list, isLoading: false, error: watchlistMocks.error }),
  useAddWatchlist: () => ({ mutateAsync: watchlistMocks.add, isPending: false, variables: undefined }),
  useRemoveWatchlist: () => ({ mutateAsync: watchlistMocks.remove, isPending: false, variables: undefined }),
  useAddPosition: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useConfirmPositionImport: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useConfirmTiantianHoldingStart: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useConfirmTiantianHoldingStartEstimates: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

beforeEach(() => {
  watchlistMocks.add.mockReset();
  watchlistMocks.remove.mockReset();
  watchlistMocks.add.mockResolvedValue(undefined);
  watchlistMocks.remove.mockResolvedValue(undefined);
  watchlistMocks.search.mockReturnValue([
    { symbol: "600036", name: "招商银行", market: "SH", type: "STOCK" },
  ]);
  watchlistMocks.list = [];
  watchlistMocks.error = null;
  watchlistMocks.emptyPortfolio = false;
});

it("adds a canonical search result and refreshes my watchlist", async () => {
  const user = userEvent.setup();
  renderWithSession(<DashboardPage />);

  await user.type(screen.getByLabelText("搜索关注标的"), "600036");
  await user.click(await screen.findByRole("button", { name: "关注 招商银行" }));

  expect(watchlistMocks.add).toHaveBeenCalledWith({ symbol: "600036" });
  expect(watchlistMocks.add).not.toHaveBeenCalledWith(
    expect.objectContaining({ userId: expect.anything(), assetName: expect.anything(), assetType: expect.anything() }),
  );
});

it("removes an existing item without changing holdings", async () => {
  const user = userEvent.setup();
  watchlistMocks.list = [
    { symbol: "600036", assetName: "招商银行", assetType: "STOCK", addedAt: "2026-09-01T08:00:00Z" },
  ];
  renderWithSession(<DashboardPage />);

  await user.click(screen.getByRole("button", { name: "取消关注 招商银行" }));

  expect(watchlistMocks.remove).toHaveBeenCalledWith("600036");
  expect(screen.getByRole("heading", { name: "当前持仓" })).toBeInTheDocument();
});

it("shows an empty watchlist even when the portfolio is empty", () => {
  watchlistMocks.emptyPortfolio = true;
  renderWithSession(<DashboardPage />);

  expect(screen.getByText(/暂未关注标的/)).toBeInTheDocument();
});

it("keeps the current list and shows the watchlist API error", async () => {
  watchlistMocks.list = [
    { symbol: "600036", assetName: "招商银行", assetType: "STOCK", addedAt: "2026-09-01T08:00:00Z" },
  ];
  watchlistMocks.error = new Error("关注列表暂不可用");
  renderWithSession(<DashboardPage />);

  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("关注列表暂不可用"));
  expect(within(screen.getByRole("region", { name: "我的关注" })).getByText("招商银行")).toBeInTheDocument();
});

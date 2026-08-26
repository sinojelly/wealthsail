import { screen } from "@testing-library/react";
import DashboardPage from "./DashboardPage";
import { renderWithSession } from "../../test/render-with-session";
import {
  navTrendFixture,
  positionsFixture,
  summaryFixture,
} from "../../test/fixtures";

vi.mock("../../api/hooks", () => ({
  usePortfolioSummary: () => ({
    data: summaryFixture,
    isLoading: false,
    isError: false,
    error: null,
  }),
  usePositions: () => ({
    data: positionsFixture,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useNavTrend: () => ({
    data: navTrendFixture,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useMarketSentiment: () => ({
    data: {
      tradeDate: null,
      totalUp: null,
      totalDown: null,
      totalFlat: null,
      limitUp: null,
      limitDown: null,
      turnover: null,
      northFlow: null,
      sentimentScore: null,
      source: "unavailable",
      updatedAt: null,
    },
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
  useUpdatePosition: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeletePosition: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

it("never presents hard-coded fill drafts as confirmed financial facts", () => {
  renderWithSession(<DashboardPage />);

  expect(screen.getByText("¥1,921,840.00")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /笔草稿/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "录入成交草稿" })).toBeDisabled();
  expect(screen.getByText("成交确认命令尚未接入，当前不会修改账本")).toBeInTheDocument();
});

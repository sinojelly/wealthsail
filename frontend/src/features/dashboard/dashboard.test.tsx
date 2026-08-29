import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
      qualityStatus: "UNAVAILABLE",
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
  useConfirmPositionImport: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

it("never presents hard-coded fill drafts as confirmed financial facts", () => {
  renderWithSession(<DashboardPage />);

  expect(screen.getByText("¥1,921,840.00")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /笔草稿/ }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "录入成交草稿" })).toBeDisabled();
  expect(
    screen.getByText("成交确认暂未开放，当前操作不会修改账本。"),
  ).toBeInTheDocument();
});

it("links to the recoverable import workflow and embeds fund range metrics", async () => {
  const user = userEvent.setup();
  renderWithSession(<DashboardPage />);

  const importLinks = screen.getAllByRole("link", {
    name: "导入 / 更新持仓",
  });
  expect(importLinks).toHaveLength(2);
  importLinks.forEach((link) =>
    expect(link).toHaveAttribute("href", "/onboarding"),
  );
  const rangeToggle = screen.getByRole("button", {
    name: "持有 895 天，区间位置 40%",
  });
  await user.click(rangeToggle);

  expect(screen.getByText("距区间最高 -20%")).toBeInTheDocument();
  expect(screen.getByText(/不等同于投资收益率/)).toBeInTheDocument();
});

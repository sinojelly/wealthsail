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
    screen.getByRole("heading", { name: "你还没有归属到自己的资产" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      "新建或导入个人账户后，这里会展示你的资产；历史待认领账户可在租户总览查看。",
    ),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "导入首笔持仓" })).toHaveAttribute(
    "href",
    "/onboarding",
  );
  expect(screen.queryByText("总资产暂不发布")).not.toBeInTheDocument();
  expect(
    screen.queryByText(/仍有 0 个持仓缺少可靠估值/),
  ).not.toBeInTheDocument();
});

it("presents dashboard gaps as concise, accessible empty states", () => {
  renderWithSession(<DashboardPage />);

  expect(
    screen.getByRole("status", { name: "暂无净值记录" }),
  ).toHaveTextContent("导入或录入持仓后，将从首个有效快照开始绘制趋势");
  expect(
    screen.getByRole("status", { name: "暂无资产配置" }),
  ).toHaveTextContent("持仓建立后，将按资产类型展示占比");
  expect(screen.getByRole("status", { name: "还没有持仓" })).toHaveTextContent(
    "导入天天基金 ZIP，或手工创建并确认期初基线",
  );
  expect(screen.getByRole("note", { name: "成交录入状态" })).toHaveTextContent(
    "成交确认暂未开放，当前操作不会修改账本",
  );
});

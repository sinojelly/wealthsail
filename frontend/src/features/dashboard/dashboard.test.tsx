import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DashboardPage from "./DashboardPage";
import { renderWithSession } from "../../test/render-with-session";
import {
  meFixture,
  navTrendFixture,
  positionsFixture,
  summaryFixture,
} from "../../test/fixtures";

const scopeCalls = vi.hoisted(() => ({
  summary: [] as unknown[],
  positions: [] as unknown[],
  nav: [] as unknown[],
}));

vi.mock("../../api/hooks", () => ({
  usePortfolioSummary: (scope: unknown) => {
    scopeCalls.summary.push(scope);
    return {
    data: summaryFixture,
    isLoading: false,
    isError: false,
    error: null,
    };
  },
  usePositions: (scope: unknown) => {
    scopeCalls.positions.push(scope);
    return {
    data: positionsFixture,
    isLoading: false,
    isError: false,
    error: null,
    };
  },
  useNavTrend: (_days: number, scope: unknown) => {
    scopeCalls.nav.push(scope);
    return {
    data: navTrendFixture,
    isLoading: false,
    isError: false,
    error: null,
    };
  },
  useMe: () => ({
    data: {
      ...meFixture,
      members: [
        ...(meFixture.members ?? []),
        {
          userId: "user-family-2",
          nickname: "家庭成员A",
          email: "member@example.test",
          role: "VIEWER",
          status: "ACTIVE",
        },
      ],
    },
    isLoading: false,
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

beforeEach(() => {
  scopeCalls.summary.length = 0;
  scopeCalls.positions.length = 0;
  scopeCalls.nav.length = 0;
});

it("defaults to my assets and keeps every portfolio query on one scope", async () => {
  const user = userEvent.setup();
  renderWithSession(<DashboardPage />);

  expect(screen.getByRole("button", { name: "我的资产" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(scopeCalls.summary.at(-1)).toEqual({ kind: "MINE" });
  expect(scopeCalls.positions.at(-1)).toEqual({ kind: "MINE" });
  expect(scopeCalls.nav.at(-1)).toEqual({ kind: "MINE" });

  await user.click(screen.getByRole("button", { name: "租户总览" }));
  expect(scopeCalls.summary.at(-1)).toEqual({ kind: "TENANT" });

  await user.selectOptions(
    screen.getByRole("combobox", { name: "成员资产" }),
    "user-family-2",
  );
  expect(scopeCalls.summary.at(-1)).toEqual({
    kind: "MEMBER",
    memberId: "user-family-2",
  });
  expect(scopeCalls.positions.at(-1)).toEqual({
    kind: "MEMBER",
    memberId: "user-family-2",
  });
  expect(scopeCalls.nav.at(-1)).toEqual({
    kind: "MEMBER",
    memberId: "user-family-2",
  });
});

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

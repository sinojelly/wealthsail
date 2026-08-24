import { screen, within } from "@testing-library/react";
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
}));

it("confirms one pending fill and updates the remaining draft count", async () => {
  const user = userEvent.setup();
  renderWithSession(<DashboardPage />);

  expect(screen.getByText("¥1,921,840.00")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /2笔草稿/ }));

  const dialog = screen.getByRole("dialog", { name: "成交草稿校对确认" });
  expect(within(dialog).getByText("科大讯飞")).toBeInTheDocument();
  await user.click(
    within(dialog).getByRole("button", { name: "确认科大讯飞成交" }),
  );

  expect(within(dialog).getByText("1 笔待确认")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /1笔草稿/ })).toBeInTheDocument();
});

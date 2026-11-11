import { render, screen } from "@testing-library/react";
import AppLoadBoundary from "../../app/AppLoadBoundary";
import FundNavTrendChart from "./components/FundNavTrendChart";

vi.mock("./components/FundNavTrendChartContent", () => {
  throw new Error("Failed to fetch chart module?private=not-for-display");
});

afterEach(() => vi.restoreAllMocks());

it("contains a rejected chart import locally and gives a real page refresh action while preserving the report and prices", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  render(
    <AppLoadBoundary>
      <h1>已保存的完整报告</h1>
      <p>参考买入区间：1.0500–1.1500</p>
      <FundNavTrendChart fundCode="012733" snapshot={null} />
    </AppLoadBoundary>,
  );

  const failure = await screen.findByRole("alert", {
    name: "012733图表加载状态",
  });
  expect(
    screen.getByRole("heading", { name: "已保存的完整报告" }),
  ).toBeVisible();
  expect(screen.getByText("参考买入区间：1.0500–1.1500")).toBeVisible();
  expect(failure).toHaveTextContent("净值图表加载失败");
  expect(failure).toHaveTextContent("刷新页面后重新加载图表");
  expect(
    screen.getByRole("button", { name: "刷新页面重载图表" }),
  ).toBeVisible();
  expect(screen.queryByRole("heading", { name: "页面加载失败" })).toBeNull();
  expect(failure).not.toHaveTextContent("private");
});

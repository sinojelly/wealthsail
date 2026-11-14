import { render, screen, within } from "@testing-library/react";
import FundPriceTracks from "./components/FundPriceTracks";
import ActionablePlan from "./components/ActionablePlan";
import { frozenPlan } from "./personal-funds.test-data";
vi.mock("./components/FundNavTrendChart", () => ({ default: () => null }));

it("uses a single core reference grid for old frozen personal plans as well", () => {
  const plan = frozenPlan();
  plan.tracks.push({ ...plan.tracks[0], track: "RIGHT_TREND" });
  render(<FundPriceTracks plan={plan} basis="原冻结" selected="LEFT_VALUE" />);
  const panel = screen.getByRole("region", { name: "参考买卖点与操作状态" });
  expect(within(panel).getAllByText("参考买入区间")).toHaveLength(1);
  expect(
    screen.queryByRole("region", { name: "原冻结右侧趋势价格区间" }),
  ).toBeNull();
});

it("also consolidates standalone v6 plan cards instead of repeating both tracks", () => {
  const plan = frozenPlan();
  plan.methodVersion = "actionable-zones-v6";
  plan.tracks.push({ ...plan.tracks[0], track: "RIGHT_TREND" });
  render(<ActionablePlan result={plan} />);
  expect(
    screen.getByRole("region", { name: "参考买卖点与操作状态" }),
  ).toBeVisible();
  expect(screen.getAllByText("参考买入区间")).toHaveLength(1);
  expect(screen.queryByRole("article", { name: "右侧趋势" })).toBeNull();
});

it("shows one current price grid with position, percentile and distances", () => {
  const plan = frozenPlan();
  plan.methodVersion = "actionable-zones-v6";
  plan.indicators.navPercentile2y = 27.3;
  plan.tracks.push({ ...plan.tracks[0], track: "RIGHT_TREND" });
  render(<FundPriceTracks plan={plan} basis="当前" />);
  const panel = screen.getByRole("region", { name: "参考买卖点与操作状态" });
  expect(within(panel).getByText("🟢 已入买区（低吸参考）")).toBeVisible();
  expect(within(panel).getAllByText("参考买入区间")).toHaveLength(1);
  expect(panel).toHaveTextContent("27.3%");
  expect(panel).toHaveTextContent("距离买区 / 卖区");
  expect(
    screen.queryByRole("region", { name: "当前右侧趋势价格区间" }),
  ).toBeNull();
  expect(screen.getByText("价格依据与风险说明").tagName).toBe("SUMMARY");
});

it("shows the real position of an incomplete history with usable dated prices", () => {
  const plan = frozenPlan();
  plan.priceRefresh = {
    state: "PARTIAL",
    navAsOf: plan.asOf,
    methodVersion: plan.methodVersion,
    reason: "SOURCE_BEHIND_CONFIRMED_NAV",
  };
  render(<FundPriceTracks plan={plan} basis="当前" />);
  expect(screen.getByText("🟢 已入买区（低吸参考）")).toBeVisible();
  expect(screen.getByText(/区间位置按上方日期计算/)).toBeVisible();
  expect(screen.getByText("1.0500–1.1500")).toBeVisible();
});

it("keeps the missing data label for a genuinely unavailable entry boundary", () => {
  const plan = frozenPlan();
  plan.tracks[0].entryZoneMin = null;
  render(<FundPriceTracks plan={plan} basis="当前" />);
  expect(screen.getByText("⚪ 净值资料补齐中")).toBeVisible();
});

it.each([
  ["0.9000", "🔴 跌破止损参考"],
  ["1.4000", "🟠 已入卖区（分批止盈）"],
  ["1.1700", "🟡 接近买区（差 1.71%）"],
  [String(1.15 / (1 - 0.0299)), "🟡 接近买区（差 2.99%）"],
  [String(1.15 / (1 - 0.0301)), "⚪ 距买区 3.01%"],
  ["1.2500", "⚪ 距买区 8.00%"],
  ["1.0300", "🟡 接近买区（差 1.94%）"],
])(
  "compares current NAV %s to the supplied reference boundaries",
  (current, label) => {
    const plan = frozenPlan();
    plan.currentPrice = current;
    render(<FundPriceTracks plan={plan} basis="当前" />);
    expect(screen.getByText(label)).toBeVisible();
  },
);

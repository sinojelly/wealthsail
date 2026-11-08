import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PersonalFundReference } from "../../api/personal-funds";
import type { PreviewFund } from "../../api/research-preview";
import FundReferenceDetails from "./components/FundReferenceDetails";
import { PreviewFundCard } from "../operations/components/PreviewFunds";
import { frozenPlan } from "./personal-funds.test-data";

vi.mock("./components/ViewportFundChart", () => ({
  default: ({
    track,
    snapshotMode,
  }: {
    track?: { track: string };
    snapshotMode?: string;
  }) => (
    <div
      role="img"
      aria-label={`${snapshotMode ?? "HISTORICAL"} ${track?.track ?? "missing"}图`}
    />
  ),
}));

function plan() {
  const value = frozenPlan("000101");
  value.tracks.push({
    ...value.tracks[0],
    track: "RIGHT_TREND",
    entryZoneMin: "2.0500",
    entryZoneMax: "2.1500",
    stopLossPrice: "1.9500",
    takeProfitT1: "2.3500",
    takeProfitT2: "2.5000",
  });
  return value;
}

function reference(): PersonalFundReference {
  return {
    key: "FUND_CN:000101",
    fundCode: "000101",
    fundMarket: "FUND_CN",
    label: "双轨基金",
    relationships: ["WATCHLIST"],
    investmentEligible: false,
    plan: plan(),
    history: null,
    confirmedNav: null,
    currentEstimate: null,
    missingReasons: [],
  };
}

it("shows both local price tracks before any selection and changes only the chart overlay", async () => {
  render(<FundReferenceDetails fund={reference()} />);
  const left = screen.getByRole("region", { name: "当前左侧低吸价格区间" });
  const right = screen.getByRole("region", { name: "当前右侧趋势价格区间" });
  expect(left).toHaveTextContent("1.05");
  expect(right).toHaveTextContent("2.05");
  expect(right).toHaveTextContent("2.35");
  expect(
    screen.getByRole("img", { name: "CURRENT_LOCAL LEFT_VALUE图" }),
  ).toBeInTheDocument();
  await userEvent.click(
    within(right).getByRole("button", { name: "右侧趋势" }),
  );
  expect(
    screen.getByRole("img", { name: "CURRENT_LOCAL RIGHT_TREND图" }),
  ).toBeInTheDocument();
  expect(left).toBeVisible();
  expect(right).toBeVisible();
});

it("keeps both historical tracks and their own frozen date visible without current prices", () => {
  const fund = {
    key: "nav:000101",
    market: "FUND_CN",
    subjectKind: "nav",
    subjectId: "000101",
    fundCode: "000101",
    label: "双轨基金",
    directionKeys: [],
    originalProposals: [],
    investmentEligible: false,
    formalEligibilityReasons: [],
    anchor: plan(),
    analysis: null,
    missingReasons: [],
  } as PreviewFund;
  render(
    <PreviewFundCard fund={fund} validatedAt="2026-10-21T12:00:00+08:00" />,
  );
  expect(
    screen.getByRole("region", { name: "研究时左侧低吸价格区间" }),
  ).toHaveTextContent("1.05");
  expect(
    screen.getByRole("region", { name: "研究时右侧趋势价格区间" }),
  ).toHaveTextContent("2.05");
  expect(screen.getByText(/冻结价格口径/)).toHaveTextContent("当前净值未读取");
  expect(screen.getByText(/冻结材料未保存净值历史/)).toBeInTheDocument();
});

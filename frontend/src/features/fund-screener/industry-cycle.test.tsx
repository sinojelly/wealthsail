import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  ActionableZoneResult,
  DailyResearch,
  ValueDirection,
} from "../../api/investment";
import ActionablePlan from "./components/ActionablePlan";
import ValueDirections from "./components/ValueDirections";

const warning = "⚠️ 行业去泡沫/等待订单期 · 谨防左侧接飞刀";
const plan = (): ActionableZoneResult => ({
  methodVersion: "actionable-zones-v3",
  subjectKind: "fund",
  subjectId: "robotics-fund",
  label: "产业研究基金",
  priceBasis: "ADJUSTED_PRICE",
  unit: "CNY",
  asOf: "2026-10-30",
  sourceUri: null,
  currentPrice: 1.15,
  indicators: { ma60: 1.3 },
  selectedTrack: "LEFT_VALUE",
  tracks: [
    {
      track: "LEFT_VALUE",
      state: "WATCH",
      entryZoneMin: 0.95,
      entryZoneMax: 1.05,
      stopLossPrice: 0.9,
      takeProfitT1: 1.25,
      takeProfitT2: 1.35,
      priceState: "BELOW_ZONE",
      reasons: [],
    },
  ],
});
const direction = (): ValueDirection => ({
  key: "robotics",
  title: "机器人与具身智能",
  thesis: "等待订单和利润验证。",
  signal: "LEFT_VALUE",
  sourceIds: [],
  macroCodes: [],
  watchFor: [],
  plans: [plan()],
});
const daily = (row: ValueDirection): DailyResearch => ({
  methodVersion: "daily-research-v1",
  state: "READY",
  runId: "run",
  reportRunId: "report",
  scheduledFor: null,
  startedAt: null,
  completedAt: null,
  asOf: "2026-10-30",
  nextRunAt: null,
  report: {
    summary: "产业研究",
    coverage: [],
    sources: [],
    events: [],
    upcoming: [],
    energy: [],
    valueDirections: [row],
  },
});

it.each([
  ["EARNINGS_DELIVERY", "🟢 业绩兑现期"],
  ["POLICY_CATALYST", "🟡 政策/事件催化期"],
  ["EARLY_COMMERCIALIZATION", "🟠 商业化早期·去伪存真期"],
] as const)(
  "shows the explicit %s stage beside the direction and full plan titles",
  (stage, label) => {
    const row = direction();
    Object.assign(row, { industryCycle: { stage } });
    const full = plan();
    Object.assign(full, { industryCycle: { stage } });
    render(
      <>
        <ValueDirections data={daily(row)} />
        <ActionablePlan result={full} />
      </>,
    );
    const value = screen.getByRole("region", { name: row.title });
    expect(within(value).getByLabelText("产业兑现阶段")).toHaveTextContent(
      label,
    );
    expect(within(value).getByText("左侧低吸观察")).toBeVisible();
    const complete = screen.getByRole("region", {
      name: "产业研究基金双轨计划",
    });
    expect(within(complete).getByLabelText("产业兑现阶段")).toHaveTextContent(
      label,
    );
  },
);

it("keeps an unclassified robotics direction pending verification instead of inferring delivery from its title", () => {
  render(<ValueDirections data={daily(direction())} />);
  expect(screen.getByLabelText("产业兑现阶段")).toHaveTextContent(
    "产业阶段待核查",
  );
  expect(screen.queryByText("🟢 业绩兑现期")).not.toBeInTheDocument();
  expect(
    screen.queryByText("🟠 商业化早期·去伪存真期"),
  ).not.toBeInTheDocument();
});

it("carries an early-commercialization warning into compact and full plan status without changing server price zones", () => {
  const row = direction();
  Object.assign(row, {
    industryCycle: {
      stage: "EARLY_COMMERCIALIZATION",
      evidenceSummary: "融资降温，尚无当期利润与回款验证。",
    },
    industryRiskWarning: warning,
  });
  const full = plan();
  Object.assign(full, {
    industryCycle: row.industryCycle,
    industryRiskWarning: warning,
  });
  const original = JSON.stringify({ row, full });
  render(
    <>
      <ValueDirections data={daily(row)} />
      <ActionablePlan result={full} />
    </>,
  );
  const compact = screen.getByRole("article", { name: "产业研究基金买卖计划" });
  const complete = screen.getByRole("region", { name: "产业研究基金双轨计划" });
  for (const card of [compact, complete]) {
    const status = within(card).getByRole("status", { name: "产业周期风险" });
    expect(status).toBeVisible();
    expect(status).toHaveTextContent(warning);
    expect(status).toHaveTextContent("仅适合极小仓位观察或等待右侧拐点");
    expect(card).toHaveTextContent("0.950–1.050");
  }
  expect(JSON.stringify({ row, full })).toBe(original);
});

it("shows supplied relative strength and the MA60 breakdown date as analysis evidence", async () => {
  const result = plan();
  Object.assign(result, {
    relativeStrength: {
      excessReturn20d: -5.25,
      excessReturn60d: -12.1,
      drawdown60d: 18.5,
      breakdownDate: "2026-09-18",
    },
  });
  render(<ActionablePlan result={result} />);
  await userEvent.click(screen.getByText("价格依据与算法"));
  const evidence = screen.getByRole("region", { name: "板块相对强弱依据" });
  expect(evidence).toBeVisible();
  expect(evidence).toHaveTextContent("20日超额收益：-5.25 个百分点");
  expect(evidence).toHaveTextContent("60日超额收益：-12.10 个百分点");
  expect(evidence).toHaveTextContent("近60日高点回落：18.50%");
  expect(evidence).toHaveTextContent(
    "本轮走弱分水岭（跌破MA60）：2026年9月18日",
  );
});

it("leaves incomplete relative strength values unknown instead of displaying zero or inventing a breakdown date", async () => {
  const result = plan();
  Object.assign(result, {
    relativeStrength: {
      excessReturn20d: 0,
      excessReturn60d: null,
      drawdown60d: null,
      breakdownDate: null,
    },
  });
  render(<ActionablePlan result={result} />);
  await userEvent.click(screen.getByText("价格依据与算法"));
  const evidence = screen.getByRole("region", { name: "板块相对强弱依据" });
  expect(evidence).toHaveTextContent("20日超额收益：0.00 个百分点");
  expect(evidence).toHaveTextContent("60日超额收益：—");
  expect(evidence).toHaveTextContent("近60日高点回落：—");
  expect(evidence).toHaveTextContent("本轮走弱分水岭（跌破MA60）：尚未确认");
});

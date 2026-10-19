import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type {
  ActionableZoneResult,
  DailyResearch,
  OpportunityContext,
} from "../../api/investment";
import MacroContextStrip from "./components/MacroContextStrip";
import QuantitativeScan from "./components/QuantitativeScan";
import ValueDirections from "./components/ValueDirections";
import { ActionablePlanEvidence } from "./components/ActionablePlan";

function plan(code: string, state = "WATCH"): ActionableZoneResult {
  return {
    methodVersion: "actionable-zones-v3",
    subjectKind: "nav",
    subjectId: code,
    fundCode: code,
    label: `${code} 主题ETF联接A`,
    priceBasis: "UNIT_NAV",
    unit: "CNY/份",
    asOf: "2026-10-16",
    sourceUri: `https://fund.eastmoney.com/${code}.html`,
    currentPrice: "1.0000",
    indicators: { rsi14: 42 },
    selectedTrack: "RIGHT_TREND",
    tracks: [
      {
        track: "RIGHT_TREND",
        state: state as "WATCH" | "CANDIDATE",
        priceState: "READY",
        entryZoneMin: "0.9800",
        entryZoneMax: "1.0200",
        stopLossPrice: "0.9000",
        takeProfitT1: "1.1500",
        takeProfitT2: "1.2400",
        reasons: [],
      },
    ],
  };
}
function daily(macroCycle = "短总纲"): DailyResearch {
  return {
    methodVersion: "daily-research-v1",
    state: "READY",
    runId: "run45",
    reportRunId: "run45",
    scheduledFor: null,
    startedAt: null,
    completedAt: null,
    asOf: "2026-10-19T07:29:00+08:00",
    nextRunAt: null,
    report: {
      macroCycle,
      summary: "研究摘要",
      coverage: [],
      sources: [],
      events: [],
      upcoming: [],
      energy: [],
      valueDirections: [],
    },
  };
}
function context(data: DailyResearch) {
  return {
    state: "READY",
    items: [],
    dailyResearch: data,
  } as OpportunityContext;
}

it("lets the user read and collapse the full macro thesis and resets for a new report", async () => {
  const text =
    "宏观风险与机会需要结合信用和产业兑现。".repeat(30) + "最后完整风险边界。";
  const data = daily(text);
  const { rerender } = render(
    <MemoryRouter>
      <MacroContextStrip data={context(data)} error={false} />
    </MemoryRouter>,
  );
  const overview = screen.getByRole("region", { name: "今日宏观总纲" });
  expect(overview).not.toHaveTextContent("最后完整风险边界。");
  await userEvent.click(
    within(overview).getByRole("button", { name: "展开今日总纲" }),
  );
  expect(overview).toHaveTextContent("最后完整风险边界。");
  expect(
    within(overview).getByRole("button", { name: "收起今日总纲" }),
  ).toHaveAttribute("aria-expanded", "true");
  await userEvent.click(
    within(overview).getByRole("button", { name: "收起今日总纲" }),
  );
  expect(overview).not.toHaveTextContent("最后完整风险边界。");
  await userEvent.click(
    within(overview).getByRole("button", { name: "展开今日总纲" }),
  );
  rerender(
    <MemoryRouter>
      <MacroContextStrip
        data={context({ ...data, reportRunId: "new-run" })}
        error={false}
      />
    </MemoryRouter>,
  );
  expect(screen.getByRole("button", { name: "展开今日总纲" })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  expect(
    screen.getByRole("region", { name: "今日宏观总纲" }),
  ).not.toHaveTextContent("最后完整风险边界。");
});

it("does not offer a meaningless expand button for a short macro thesis", () => {
  render(
    <MemoryRouter>
      <MacroContextStrip data={context(daily())} error={false} />
    </MemoryRouter>,
  );
  expect(
    screen.queryByRole("button", { name: /今日总纲/ }),
  ).not.toBeInTheDocument();
});

it("reads the NAV risk method in Chinese without rewriting the saved report", () => {
  const data = daily("净值风险尺度为NAV_ABS_CHANGE_STD_V1，非真实ATR。");
  render(
    <MemoryRouter>
      <MacroContextStrip data={context(data)} error={false} />
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("region", { name: "今日宏观总纲" }),
  ).toHaveTextContent("净值风险尺度为净值日变动标准差，非真实ATR。");
  expect(data.report!.macroCycle).toContain("NAV_ABS_CHANGE_STD_V1");
});

it("puts a triggered buy zone before a geometric WATCH right-track zone, including old scan payloads", async () => {
  const data = daily();
  data.report!.quantitativeScan = {
    methodVersion: "fund-quant-scan-v1",
    asOf: data.asOf!,
    minimumHistorySamples: 220,
    coverage: { pricedFundCount: 2, targetHistoryFundCount: 2 },
    items: [plan("161725"), plan("000051", "CANDIDATE")].map((row) => ({
      ...row,
      signals: [
        {
          track: "RIGHT_TREND",
          triggerState:
            row.tracks[0].state === "CANDIDATE"
              ? ("CANDIDATE" as const)
              : ("WATCH" as const),
          inEntryZone: true,
          distanceToEntryPct: 0,
        },
      ],
    })),
  };
  render(<QuantitativeScan data={data} />);
  await userEvent.click(screen.getByText(/查看全市场/));
  const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
  expect(rows.map((row) => row.getAttribute("data-fund-code"))).toEqual([
    "000051",
    "161725",
  ]);
  expect(rows[0]).toHaveTextContent("📈已入右侧买区");
  expect(rows[1]).toHaveTextContent("价格近均线·待趋势触发");
  expect(rows[1].querySelector(".op-scan-hit")).toBeNull();
});

it("keeps a WATCH direction from displaying an active buy-zone badge and binds the unit to its quote", () => {
  const data = daily();
  data.report!.valueDirections = [
    {
      key: "consumer",
      title: "消费",
      thesis: "研究",
      signal: "WATCH",
      sourceIds: [],
      macroCodes: [],
      watchFor: [],
      plans: [plan("161725")],
    },
  ];
  render(<ValueDirections data={data} />);
  const card = screen.getByRole("article", { name: /买卖计划/ });
  expect(card.querySelector(".op-value-hit")).toBeNull();
  expect(card).toHaveTextContent("价格近均线·待趋势触发");
  expect(card.querySelector(".op-price-amount")).toHaveTextContent(
    "1.0000 元/份",
  );
});

it("explains the new reward-risk floors without leaking an internal basis code", () => {
  render(
    <ActionablePlanEvidence
      result={
        {
          ...plan("161725"),
          parameters: {
            minimumRewardRiskT1: 1.5,
            minimumRewardRiskT2: 2.4,
            rewardRiskBasis: "DISPLAYED_ENTRY_MID_AND_STOP",
          },
        } as ActionableZoneResult
      }
    />,
  );
  expect(screen.getByText(/参数：/)).toHaveTextContent(
    "目标一最低风险收益比=1.5",
  );
  expect(screen.getByText(/参数：/)).toHaveTextContent(
    "目标二最低风险收益比=2.4",
  );
  expect(screen.getByText(/参数：/)).toHaveTextContent(
    "风险基准=显示买区中点与止损",
  );
});

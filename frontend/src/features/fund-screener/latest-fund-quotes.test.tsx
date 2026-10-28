import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import * as funds from "../../api/funds";
import * as study from "../../api/study-api";
import TrackedFundWatch from "./components/TrackedFundWatch";
import type { DailyResearch } from "../../api/investment";
import ValueDirections from "./components/ValueDirections";

vi.mock("../../api/funds", () => ({
  getLatestFundQuotes: vi.fn(),
  getFundNavHistory: vi.fn().mockRejectedValue(new Error("offline")),
}));
vi.mock("../../api/study-api", () => ({
  getStudyTracks: vi.fn(),
  setFundWatchStatus: vi.fn(),
  setStudyStatus: vi.fn(),
}));

const report = (): DailyResearch =>
  ({
    state: "READY",
    reportRunId: "report-close",
    report: {
      valueDirections: [
        {
          key: "security",
          title: "证券观察",
          thesis: "观察行业",
          signal: "LEFT_VALUE",
          sourceIds: [],
          macroCodes: [],
          watchFor: [],
          plans: [
            {
              methodVersion: "test",
              subjectKind: "nav",
              subjectId: "004069",
              fundCode: "004069",
              label: "证券联接A",
              priceBasis: "UNIT_NAV",
              unit: "CNY",
              asOf: "2026-10-27",
              currentPrice: "1.0876",
              currentChangePct: "-2.04",
              indicators: {},
              parameters: {},
              selectedTrack: "LEFT_VALUE",
              tracks: [
                {
                  track: "LEFT_VALUE",
                  state: "CANDIDATE",
                  entryZoneMin: "1.0764",
                  entryZoneMax: "1.0918",
                  stopLossPrice: "1.0484",
                  takeProfitT1: "1.1535",
                  takeProfitT2: "1.1698",
                  reasons: [],
                  missingEvidence: [],
                },
              ],
            },
          ],
        },
      ],
      sources: [],
    },
  }) as unknown as DailyResearch;

it("renders the frozen card immediately then shows an independent after-close estimate with actual time", async () => {
  let finish!: (value: funds.LatestFundQuotes) => void;
  vi.mocked(funds.getLatestFundQuotes).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const data = report();
  const original = JSON.stringify(data);
  render(<ValueDirections data={data} />);
  const card = screen.getByRole("article", { name: "证券联接A买卖计划" });
  expect(card).toHaveTextContent("1.0876");
  expect(card).toHaveTextContent("1.0764–1.0918");
  await waitFor(() => expect(funds.getLatestFundQuotes).toHaveBeenCalled());
  finish({
    items: [
      {
        fundCode: "004069",
        confirmedNav: null,
        currentEstimate: {
          value: "1.1234",
          asOf: "2026-10-28T16:04:00+08:00",
          changePct: "3.29",
          usable: false,
          afterClose: true,
        },
      },
    ],
    asOf: "2026-10-28T16:10:00+08:00",
  });
  await waitFor(() =>
    expect(card).toHaveTextContent(
      "基金估算净值：1.1234+3.29%（2026年10月28日 16:04）",
    ),
  );
  expect(within(card).getByLabelText("估算涨跌幅")).toHaveClass("positive");
  expect(card).toHaveTextContent("最新确认净值：1.0876");
  expect(card).toHaveTextContent("1.0764–1.0918");
  expect(card).toHaveTextContent("估算值仅供参考，不用于当前触发。");
  expect(JSON.stringify(data)).toBe(original);
});

it("uses a newer official disclosed quote without revising the research coordinate", async () => {
  vi.mocked(funds.getLatestFundQuotes).mockResolvedValue({
    items: [
      {
        fundCode: "006999",
        currentEstimate: null,
        confirmedNav: {
          value: "1.1200",
          asOf: "2026-10-28",
          changePct: "2.98",
          usable: true,
        },
      },
    ],
    asOf: "2026-10-28T21:00:00+08:00",
  });
  const data = report();
  const plan = data.report!.valueDirections![0].plans[0];
  plan.subjectId = plan.fundCode = "006999";
  render(<ValueDirections data={data} />);
  const card = screen.getByRole("article", { name: /证券联接A.*买卖计划/ });
  await waitFor(() => expect(card).toHaveTextContent("最新确认净值：1.1200"));
  expect(card).toHaveTextContent("(10.28)");
  expect(card).toHaveTextContent("2026年10月27日 · 左侧研究坐标");
  expect(card).toHaveTextContent("1.0764–1.0918");
});

it("dates a historical sell conclusion when a newer confirmed NAV has left its sell band", async () => {
  const data = report();
  const plan = data.report!.valueDirections![0].plans[0];
  plan.subjectId = plan.fundCode = "008297";
  plan.methodVersion = "actionable-zones-v5";
  plan.currentPrice = "1.7500";
  plan.tracks[0].inSellZone = true;
  plan.tracks[0].takeProfitT1 = "1.7200";
  plan.tracks[0].takeProfitT2 = "1.8000";
  vi.mocked(funds.getLatestFundQuotes).mockResolvedValue({
    items: [
      {
        fundCode: "008297",
        currentEstimate: null,
        confirmedNav: { value: "1.5000", asOf: "2026-10-28", usable: true },
      },
    ],
    asOf: "2026-10-28T21:00:00+08:00",
  });
  render(<ValueDirections data={data} />);
  const card = screen.getByRole("article", { name: /证券联接A.*买卖计划/ });
  await waitFor(() => expect(card).toHaveTextContent("最新确认净值：1.5000"));
  expect(card).not.toHaveTextContent("当前净值已进入建议卖出区间");
  expect(card).toHaveTextContent("研究时已进入建议卖出区间");
  expect(card).toHaveTextContent("最新净值已更新，请按当前价格重新评估");
});

it("refreshes the server judgment once new quotes fill the server cache after the initial table read", async () => {
  const plan = {
    ...report().report!.valueDirections![0].plans[0],
    subjectId: "009911",
    fundCode: "009911",
  };
  const track: study.StudyTrack = {
    id: "cold-watch",
    title: "证券研究",
    enabled: true,
    generation: 1,
    status: "OBSERVING",
    createdAt: "2026-10-27",
    updatedAt: "2026-10-28",
    scope: { fundCode: "009911", fundMarket: "FUND_CN" },
    unreadCount: 0,
    priceWatch: {
      frozenPlan: plan,
      confirmedNav: null,
      estimate1430: null,
      currentEstimate: null,
      holding: { state: "NONE" },
      state: "UNKNOWN",
      basis: null,
      asOf: null,
      reason: "等待当前行情",
    },
  };
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [track],
    total: 1,
  });
  let finish!: (value: funds.LatestFundQuotes) => void;
  vi.mocked(funds.getLatestFundQuotes).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TrackedFundWatch actor="quotes-test" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(
    await screen.findByText("行情待更新", { selector: "strong" }),
  ).toBeVisible();
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [
      {
        ...track,
        priceWatch: {
          ...track.priceWatch!,
          state: "ENTRY_ZONE",
          basis: "ESTIMATE_INTRADAY",
          asOf: "2026-10-28T14:40:00+08:00",
          reason: "按当前估值核对",
        },
      },
    ],
    total: 1,
  });
  finish({
    items: [
      {
        fundCode: "009911",
        confirmedNav: null,
        currentEstimate: {
          value: "1.0800",
          asOf: "2026-10-28T14:40:00+08:00",
          usable: true,
        },
      },
    ],
    asOf: "2026-10-28T14:40:00+08:00",
  });
  expect(
    await screen.findByText("🎯已入买入击球区", { selector: "strong" }),
  ).toBeVisible();
  expect(study.getStudyTracks).toHaveBeenCalledTimes(2);
  client.clear();
});

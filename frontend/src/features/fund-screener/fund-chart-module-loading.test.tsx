import { Suspense } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { FundNavHistory } from "../../api/funds";
import FundNavTrendChart from "./components/FundNavTrendChart";
import { frozenPlan } from "./personal-funds.test-data";

const snapshot: FundNavHistory = {
  methodVersion: "fund-unit-nav-trend-v1",
  fundCode: "012733",
  fundName: "真实快照基金",
  period: "2y",
  priceBasis: "UNIT_NAV",
  unit: "CNY/份",
  state: "READY",
  asOf: "2026-11-09",
  retrievedAt: null,
  sourceUri: "https://example.org/frozen-nav",
  points: [
    { date: "2026-11-08", unitNav: "1.0400" },
    { date: "2026-11-09", unitNav: "1.1200" },
  ],
  coverage: {
    startDate: "2026-11-08",
    endDate: "2026-11-09",
    fullHistory: false,
    requestedStartDate: null,
    sampleCount: 2,
  },
  summary: {
    minimumNav: "1.0400",
    maximumNav: "1.1200",
    currentNav: "1.1200",
    percentilePct: 75,
    percentileMethod: "EMPIRICAL_MIDRANK_V1",
  },
  refresh: { state: "IDLE", runId: null, pollAfterMs: null },
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("keeps the report and prices visible while the real chart module loads, then preserves frozen data and controls", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    width: 600,
    height: 220,
    top: 0,
    right: 600,
    bottom: 220,
    left: 0,
    toJSON: () => ({}),
  });
  const track = frozenPlan("012733").tracks[0];
  render(
    <Suspense fallback={<p>正在加载工作区…</p>}>
      <h1>已保存的完整报告</h1>
      <p>
        参考买入区间：{track.entryZoneMin}–{track.entryZoneMax}
      </p>
      <FundNavTrendChart
        fundCode="012733"
        track={track}
        confirmedNavAsOf="2026-11-09"
        snapshot={snapshot}
        snapshotMode="CURRENT_LOCAL"
      />
    </Suspense>,
  );

  expect(
    screen.getByRole("heading", { name: "已保存的完整报告" }),
  ).toBeVisible();
  expect(screen.getByText(/参考买入区间/)).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("正在加载净值图表组件");
  expect(screen.queryByText("正在加载工作区…")).not.toBeInTheDocument();

  await act(async () => vi.dynamicImportSettled());
  const chart = await screen.findByRole("img", { name: "012733单位净值走势" });
  await waitFor(() =>
    expect(chart.querySelectorAll(".recharts-reference-area")).toHaveLength(2),
  );
  expect(screen.getByText(/图中最新净值/)).toHaveTextContent("1.1200");
  expect(screen.getByText(/图中最新净值/)).toHaveTextContent("2026年11月9日");
  expect(
    screen.queryByText(/曲线末点早于最新确认净值/),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "曲线数据来源" })).toHaveAttribute(
    "href",
    snapshot.sourceUri,
  );
  await userEvent.click(screen.getByRole("button", { name: "近半年" }));
  expect(screen.getByRole("button", { name: "近半年" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(fetch).not.toHaveBeenCalled();
});

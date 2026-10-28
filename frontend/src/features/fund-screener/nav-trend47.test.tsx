import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ActionableZoneTrack } from "../../api/investment";
import FundNavTrendChart from "./components/FundNavTrendChart";

const track: ActionableZoneTrack = {
  track: "LEFT_VALUE",
  state: "CANDIDATE",
  priceState: "READY",
  entryZoneMin: "1.1",
  entryZoneMax: "1.2",
  stopLossPrice: "0.9",
  takeProfitT1: "1.6",
  takeProfitT2: "1.8",
  reasons: [],
};
function history(code: string, period = "2y", extra = {}) {
  return {
    methodVersion: "fund-unit-nav-trend-v1",
    fundCode: code,
    fundName: "真实测试基金",
    period,
    priceBasis: "UNIT_NAV",
    unit: "CNY/份",
    state: "READY",
    asOf: "2026-10-20",
    retrievedAt: "2026-10-20T10:00:00+08:00",
    sourceUri: `https://fund.eastmoney.com/${code}.html`,
    points: [
      { date: "2026-10-18", unitNav: "1.0000" },
      { date: "2026-10-19", unitNav: "1.5000" },
      { date: "2026-10-20", unitNav: "1.2000" },
    ],
    coverage: {
      startDate: "2026-10-18",
      endDate: "2026-10-20",
      fullHistory: false,
      requestedStartDate: "2025-10-20",
      sampleCount: 3,
    },
    summary: {
      minimumNav: "1.0000",
      maximumNav: "1.5000",
      currentNav: "1.2000",
      percentilePct: 50,
      percentileMethod: "MIDRANK",
    },
    refresh: { state: "IDLE", runId: null, pollAfterMs: 1000 },
    ...extra,
  };
}
function response(data: ReturnType<typeof history>) {
  return new Response(JSON.stringify({ data, meta: {} }), { status: 200 });
}
beforeEach(() =>
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
  }),
);
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it("defaults to two-year real NAV and draws frozen buy and sell bands, including T2 above historical highs", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      expect(String(url)).toBe("/api/v1/funds/100101/nav-history?period=2y");
      return response(history("100101", "2y"));
    }),
  );
  render(<FundNavTrendChart fundCode="100101" track={track} />);
  expect(await screen.findByText(/区间最低 1.0000/)).toHaveTextContent(
    "最高 1.5000",
  );
  expect(screen.getByText(/该周期 50.0% 分位/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "近2年" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const graph = screen.getByRole("img", { name: "100101单位净值走势" });
  expect(graph).toBeInTheDocument();
  await waitFor(() =>
    expect(graph.querySelectorAll(".recharts-reference-line")).toHaveLength(1),
  );
  expect(graph.querySelectorAll(".recharts-reference-area")).toHaveLength(2);
  expect(screen.getByText("建议买入区间")).toBeInTheDocument();
  expect(screen.getByText("止损参考")).toBeInTheDocument();
  expect(screen.getByText("建议卖出区间")).toBeInTheDocument();
});

it("keeps a legacy single target as a line without fabricating a sell range", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response(history("100113"))),
  );
  render(
    <FundNavTrendChart
      fundCode="100113"
      track={{ ...track, takeProfitT2: null }}
    />,
  );
  await screen.findByText(/区间最低/);
  const graph = screen.getByRole("img", { name: "100113单位净值走势" });
  await waitFor(() =>
    expect(graph.querySelectorAll(".recharts-reference-line")).toHaveLength(2),
  );
  expect(graph.querySelectorAll(".recharts-reference-area")).toHaveLength(1);
  expect(screen.queryByText("建议卖出区间")).not.toBeInTheDocument();
  expect(screen.getByText("卖出参考下沿（T1）")).toBeInTheDocument();
});

it("switches to full disclosed history without claiming an invented inception date", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      const period = new URL(String(url), "http://local.test").searchParams.get(
        "period",
      )!;
      return response(
        history("100102", period, {
          coverage: {
            startDate: "2026-07-01",
            endDate: "2026-10-20",
            fullHistory: period === "all",
            requestedStartDate: null,
            sampleCount: 3,
          },
        }),
      );
    }),
  );
  render(<FundNavTrendChart fundCode="100102" track={track} />);
  await screen.findByText(/区间最低/);
  await userEvent.click(screen.getByRole("button", { name: "成立以来" }));
  expect(
    await screen.findByText("来源完整披露历史，始于2026年7月1日"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "成立以来" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

it("keeps a real curve visible after switching through every observation period", async () => {
  const requested: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      const period = new URL(String(url), "http://local.test").searchParams.get(
        "period",
      )!;
      requested.push(period);
      return response(history("100112", period));
    }),
  );
  render(<FundNavTrendChart fundCode="100112" track={track} />);
  await screen.findByText(/区间最低 1.0000/);
  for (const [period, label] of [
    ["6m", "近半年"],
    ["1y", "近1年"],
    ["2y", "近2年"],
    ["all", "成立以来"],
  ]) {
    await userEvent.click(screen.getByRole("button", { name: label }));
    if (period !== "2y")
      await waitFor(() => expect(requested.at(-1)).toBe(period));
    expect(screen.getByRole("button", { name: label })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      await screen.findByRole("img", { name: "100112单位净值走势" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("净值走势暂不可用")).not.toBeInTheDocument();
  }
  expect(requested).toEqual(["2y", "6m", "1y", "all"]);
});

it("shares a pending history request across duplicate cards and keeps it alive while one consumer remains", async () => {
  let complete!: (value: Response) => void;
  let observedSignal: AbortSignal | undefined;
  const fetcher = vi.fn((_url, init) => {
    observedSignal = init.signal;
    return new Promise<Response>((resolve) => {
      complete = resolve;
    });
  });
  vi.stubGlobal("fetch", fetcher);
  const { rerender } = render(
    <>
      <FundNavTrendChart fundCode="100103" track={track} />
      <FundNavTrendChart fundCode="100103" track={track} />
    </>,
  );
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  rerender(<FundNavTrendChart fundCode="100103" track={track} />);
  expect(observedSignal?.aborted).toBe(false);
  await act(async () => complete(response(history("100103"))));
  expect(await screen.findByText(/区间最低 1.0000/)).toBeInTheDocument();
});

it("aborts an unobserved request and never shows an old fund response in the replacement card", async () => {
  let oldComplete!: (value: Response) => void;
  let oldSignal: AbortSignal | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((url, init) => {
      if (String(url).includes("100104")) {
        oldSignal = init.signal;
        return new Promise<Response>((resolve) => {
          oldComplete = resolve;
        });
      }
      return Promise.resolve(
        response(
          history("100105", "2y", {
            summary: {
              minimumNav: "2",
              maximumNav: "3",
              currentNav: "2.5",
              percentilePct: 50,
              percentileMethod: "MIDRANK",
            },
          }),
        ),
      );
    }),
  );
  const { rerender } = render(
    <FundNavTrendChart fundCode="100104" track={track} />,
  );
  await waitFor(() => expect(oldSignal).toBeDefined());
  rerender(<FundNavTrendChart fundCode="100105" track={track} />);
  expect(await screen.findByText(/区间最低 2.0000/)).toBeInTheDocument();
  await waitFor(() => expect(oldSignal?.aborted).toBe(true));
  await act(async () => oldComplete(response(history("100104"))));
  expect(screen.queryByText(/区间最低 1.0000/)).not.toBeInTheDocument();
});

it("retains a usable partial curve while queued deep history is being filled", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      response(
        history("100106", "2y", {
          state: "PARTIAL",
          refresh: { state: "QUEUED", runId: "run", pollAfterMs: 10000 },
        }),
      ),
    ),
  );
  render(<FundNavTrendChart fundCode="100106" track={track} />);
  expect(await screen.findByText(/历史净值正在补齐/)).toBeInTheDocument();
  expect(screen.getByText(/区间最低 1.0000/)).toBeInTheDocument();
});

it("does not draw another fund or cumulative NAV as this fund unit NAV", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      response(history("999999", "2y", { priceBasis: "ACCUMULATED_NAV" })),
    ),
  );
  render(<FundNavTrendChart fundCode="100107" track={track} />);
  expect(await screen.findByText(/净值走势暂不可用/)).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it.each([
  ["100109", { points: [{ date: "2026-10-22", unitNav: "1.2" }] }],
  ["100110", { unit: "USD/份" }],
])(
  "rejects future observations or another currency before plotting %s",
  async (code, extra) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response(history(code, "2y", extra))),
    );
    render(<FundNavTrendChart fundCode={code} track={track} />);
    expect(await screen.findByText(/净值走势暂不可用/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  },
);

it("stops automatic history polling after ninety seconds while leaving an explicit reread action", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-20T08:30:00+08:00"));
  const fetcher = vi.fn(async () =>
    response(
      history("100108", "2y", {
        state: "PARTIAL",
        refresh: { state: "RUNNING", runId: "running", pollAfterMs: 10000 },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<FundNavTrendChart fundCode="100108" track={track} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(95000);
  });
  expect(screen.getByRole("button", { name: "重新读取净值" })).toBeEnabled();
  expect(fetcher.mock.calls.length).toBeLessThanOrEqual(10);
  const completed = fetcher.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(fetcher).toHaveBeenCalledTimes(completed);
  expect(screen.getByText(/区间最低 1.0000/)).toBeInTheDocument();
});

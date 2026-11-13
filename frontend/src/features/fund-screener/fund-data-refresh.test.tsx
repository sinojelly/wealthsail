import { act, render, renderHook, waitFor } from "@testing-library/react";
import * as funds from "../../api/funds";
import {
  refreshLatestFundQuotes,
  useLatestFundQuote,
} from "./latest-quote-resource";
import { useFundNavHistory } from "./nav-history-resource";

vi.mock("../../api/funds", () => ({
  getLatestFundQuotes: vi.fn(),
  refreshFundQuotes: vi.fn(),
  getFundNavHistory: vi.fn(),
  refreshFundNavHistory: vi.fn(),
}));
function quote(code: string): funds.LatestFundQuotes {
  return {
    asOf: "2026-11-12T10:00:00+08:00",
    items: [
      {
        fundCode: code,
        confirmedNav: { value: "1.25", asOf: "2026-11-11", usable: true },
        currentEstimate: null,
      },
    ],
  };
}
function history(
  code: string,
  state: "IDLE" | "QUEUED" = "IDLE",
): funds.FundNavHistory {
  return {
    methodVersion: "fund-unit-nav-trend-v1",
    fundCode: code,
    fundName: "测试基金",
    period: "2y",
    priceBasis: "UNIT_NAV",
    unit: "CNY/份",
    state: "PARTIAL",
    asOf: "2026-11-11",
    retrievedAt: null,
    sourceUri: null,
    points: [{ date: "2026-11-11", unitNav: "1.25" }],
    coverage: {
      startDate: "2026-11-11",
      endDate: "2026-11-11",
      fullHistory: false,
      requestedStartDate: null,
      sampleCount: 1,
    },
    summary: {
      minimumNav: "1.25",
      maximumNav: "1.25",
      currentNav: "1.25",
      percentilePct: 50,
      percentileMethod: "MIDRANK",
    },
    refresh: {
      state,
      runId: state === "QUEUED" ? "queued" : null,
      pollAfterMs: 1000,
    },
  };
}
beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.useRealTimers());

it("keeps quote mount/focus reads local and only uses POST refresh after an explicit action", async () => {
  vi.mocked(funds.getLatestFundQuotes).mockResolvedValue(quote("910101"));
  vi.mocked(funds.refreshFundQuotes).mockResolvedValue(quote("910101"));
  const { result } = renderHook(() => useLatestFundQuote("910101"));
  await waitFor(() => expect(result.current.data?.fundCode).toBe("910101"));
  expect(funds.refreshFundQuotes).not.toHaveBeenCalled();
  act(() => window.dispatchEvent(new Event("focus")));
  await waitFor(() =>
    expect(funds.getLatestFundQuotes).toHaveBeenCalledTimes(2),
  );
  expect(funds.refreshFundQuotes).not.toHaveBeenCalled();
  await waitFor(() => expect(result.current.loading).toBe(false));
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await waitFor(() =>
    expect(funds.getLatestFundQuotes).toHaveBeenCalledTimes(3),
  );
  expect(funds.refreshFundQuotes).not.toHaveBeenCalled();
  await waitFor(() => expect(result.current.loading).toBe(false));
  act(() => refreshLatestFundQuotes());
  await waitFor(() =>
    expect(funds.refreshFundQuotes).toHaveBeenCalledWith(["910101"]),
  );
  await waitFor(() => expect(result.current.loading).toBe(false));
});

it("discards a queued refresh intent when its consumers leave before the initial read finishes", async () => {
  let finish!: (value: funds.LatestFundQuotes) => void;
  vi.mocked(funds.getLatestFundQuotes).mockReturnValueOnce(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const first = renderHook(() => useLatestFundQuote("910106"));
  await waitFor(() => expect(funds.getLatestFundQuotes).toHaveBeenCalled());
  act(() => refreshLatestFundQuotes());
  first.unmount();
  await act(async () => finish(quote("910106")));
  expect(funds.refreshFundQuotes).not.toHaveBeenCalled();
  vi.mocked(funds.getLatestFundQuotes).mockResolvedValue(quote("910106"));
  const second = renderHook(() => useLatestFundQuote("910106"));
  await waitFor(() => expect(second.result.current.loading).toBe(false));
  expect(funds.refreshFundQuotes).not.toHaveBeenCalled();
});

it("deduplicates observed funds and caps explicit provider batches at the endpoint limit", async () => {
  const codes = Array.from({ length: 33 }, (_, index) =>
    String(911000 + index),
  );
  const response = (values: string[]): funds.LatestFundQuotes => ({
    asOf: "2026-11-12",
    items: values.flatMap((code) => quote(code).items),
  });
  vi.mocked(funds.getLatestFundQuotes).mockImplementation(async (values) =>
    response(values),
  );
  vi.mocked(funds.refreshFundQuotes).mockImplementation(async (values) =>
    response(values),
  );
  function Probe({ code }: { code: string }) {
    useLatestFundQuote(code);
    return null;
  }
  render(
    <>
      {[...codes, codes[0]].map((code, index) => (
        <Probe key={index} code={code} />
      ))}
    </>,
  );
  await waitFor(() =>
    expect(funds.getLatestFundQuotes).toHaveBeenCalledTimes(2),
  );
  await act(async () => {
    await Promise.resolve();
  });
  act(() => refreshLatestFundQuotes());
  await waitFor(() => expect(funds.refreshFundQuotes).toHaveBeenCalledTimes(2));
  const batches = vi
    .mocked(funds.refreshFundQuotes)
    .mock.calls.map(([values]) => values);
  expect(batches.every((values) => values.length <= 32)).toBe(true);
  expect(batches.flat().sort()).toEqual(codes);
});

it("retains an explicit quote refresh requested while the initial local read is pending", async () => {
  let finish!: (value: funds.LatestFundQuotes) => void;
  vi.mocked(funds.getLatestFundQuotes).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  vi.mocked(funds.refreshFundQuotes).mockResolvedValue(quote("910102"));
  const { result } = renderHook(() => useLatestFundQuote("910102"));
  await waitFor(() => expect(funds.getLatestFundQuotes).toHaveBeenCalled());
  act(() => refreshLatestFundQuotes());
  expect(funds.refreshFundQuotes).not.toHaveBeenCalled();
  await act(async () => finish(quote("910102")));
  await waitFor(() => expect(funds.refreshFundQuotes).toHaveBeenCalledTimes(1));
  expect(result.current.data?.fundCode).toBe("910102");
});

it("does not fall back to provider refresh after a failed local quote read", async () => {
  vi.mocked(funds.getLatestFundQuotes).mockRejectedValue(new Error("offline"));
  const { result } = renderHook(() => useLatestFundQuote("910103"));
  await waitFor(() => expect(result.current.failed).toBe(true));
  expect(funds.refreshFundQuotes).not.toHaveBeenCalled();
});

it("mounts NAV with GET and explicitly queues refresh before GET-only polling", async () => {
  vi.useFakeTimers();
  vi.mocked(funds.getFundNavHistory).mockResolvedValue(history("910104"));
  vi.mocked(funds.refreshFundNavHistory).mockResolvedValue(
    history("910104", "QUEUED"),
  );
  const { result } = renderHook(() => useFundNavHistory("910104", "2y"));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(funds.getFundNavHistory).toHaveBeenCalledTimes(1);
  expect(funds.refreshFundNavHistory).not.toHaveBeenCalled();
  await act(async () => result.current.retry());
  expect(funds.refreshFundNavHistory).toHaveBeenCalledWith(
    "910104",
    "2y",
    expect.any(AbortSignal),
  );
  expect(result.current.data?.refresh.state).toBe("QUEUED");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(funds.getFundNavHistory).toHaveBeenCalledTimes(2);
  expect(funds.refreshFundNavHistory).toHaveBeenCalledTimes(1);
  expect(result.current.data?.refresh.state).toBe("IDLE");
});

it("keeps the old curve and exposes a failed explicit NAV refresh without creating an automatic retry loop", async () => {
  vi.mocked(funds.getFundNavHistory).mockResolvedValue(history("910105"));
  vi.mocked(funds.refreshFundNavHistory).mockRejectedValue(
    new Error("refresh unavailable"),
  );
  const { result } = renderHook(() => useFundNavHistory("910105", "2y"));
  await waitFor(() => expect(result.current.data?.points).toHaveLength(1));
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.failed).toBe(true));
  expect(result.current.data?.points).toHaveLength(1);
  expect(funds.getFundNavHistory).toHaveBeenCalledTimes(1);
  expect(funds.refreshFundNavHistory).toHaveBeenCalledTimes(1);
});

it("retains a failed explicit activation instead of hiding it behind an automatic local read", async () => {
  vi.mocked(funds.refreshFundNavHistory).mockRejectedValue(
    new Error("refresh unavailable"),
  );
  const { result, rerender } = renderHook(
    ({ enabled }) => useFundNavHistory("910107", "2y", enabled),
    { initialProps: { enabled: false } },
  );
  expect(funds.getFundNavHistory).not.toHaveBeenCalled();
  await act(async () => result.current.retry());
  rerender({ enabled: true });
  await waitFor(() => expect(result.current.failed).toBe(true));
  expect(funds.refreshFundNavHistory).toHaveBeenCalledTimes(1);
  expect(funds.getFundNavHistory).not.toHaveBeenCalled();
});

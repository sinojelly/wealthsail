import {
  fmtPercentileLabel,
  getLatestFundQuotes,
  refreshFundQuotes,
  getFundNavHistory,
  refreshFundNavHistory,
} from "./funds";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("labels a NAV range position without implying valuation", () => {
  expect(fmtPercentileLabel(72.5)).toBe("区间位置 72.50% · 区间偏高");
  expect(fmtPercentileLabel(null)).toBe("--");
});

it("uses read-only GET for ordinary quotes and an explicit POST for provider refresh", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(
      async () =>
        new Response(
          JSON.stringify({ data: { items: [], asOf: "2026-11-12" }, meta: {} }),
          { status: 200 },
        ),
    );
  vi.stubGlobal("fetch", fetcher);
  await getLatestFundQuotes(["000002", "000001", "000002"]);
  await refreshFundQuotes(["000002", "000001", "000002"]);
  expect(
    fetcher.mock.calls.map(([url, init]) => [
      String(url),
      init.method ?? "GET",
    ]),
  ).toEqual([
    ["/api/v1/funds/quotes?codes=000001%2C000002", "GET"],
    ["/api/v1/funds/quotes/refresh?codes=000001%2C000002", "POST"],
  ]);
});

it("keeps NAV period selection on GET and sends a cancellable explicit refresh POST", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(
      async () =>
        new Response(JSON.stringify({ data: {}, meta: {} }), { status: 200 }),
    );
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  await getFundNavHistory("000001", "all");
  await refreshFundNavHistory("000001", "all", controller.signal);
  expect(
    fetcher.mock.calls.map(([url, init]) => [
      String(url),
      init.method ?? "GET",
    ]),
  ).toEqual([
    ["/api/v1/funds/000001/nav-history?period=all", "GET"],
    ["/api/v1/funds/000001/nav-history/refresh?period=all", "POST"],
  ]);
  expect(fetcher.mock.calls[1][1].signal).toBe(controller.signal);
});

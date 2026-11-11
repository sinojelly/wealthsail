import {
  installFrontendPerformance,
  recordFrontendTiming,
} from "./performance";

const originalUrl = window.location.href;

afterEach(() => {
  window.history.replaceState(null, "", originalUrl);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("keeps browser timing diagnostics disabled by default", () => {
  const log = vi.spyOn(console, "info").mockImplementation(() => {});
  const stop = installFrontendPerformance();
  recordFrontendTiming("auth_ready", 120);
  window.dispatchEvent(new Event("load"));
  expect(log).not.toHaveBeenCalled();
  stop();
});

it("reports startup and asset transfer costs without recording query strings or private API resources", async () => {
  window.history.replaceState(null, "", "/fund-screener?perf=1&private=secret");
  const log = vi.spyOn(console, "info").mockImplementation(() => {});
  vi.stubGlobal("PerformanceObserver", undefined);
  vi.stubGlobal("performance", {
    now: () => 1200,
    getEntriesByType: (type: string) =>
      type === "navigation"
        ? [
            {
              startTime: 0,
              requestStart: 50,
              responseStart: 150,
              responseEnd: 200,
              domContentLoadedEventEnd: 1000,
              loadEventEnd: 1150,
            },
          ]
        : [
            {
              name: `${window.location.origin}/assets/index-abc.js?private=secret`,
              duration: 750,
              fetchStart: 25,
              requestStart: 50,
              responseStart: 150,
              responseEnd: 700,
              transferSize: 1500,
              encodedBodySize: 1200,
              decodedBodySize: 6500,
              nextHopProtocol: "h2",
              privateMetadata: "secret",
            },
            {
              name: `${window.location.origin}/api/v1/private?token=secret`,
              duration: 50,
            },
            {
              name: "https://third-party.example/assets/private.js",
              duration: 80,
            },
          ],
  });

  const stop = installFrontendPerformance();
  recordFrontendTiming("auth_ready", 100.126);
  recordFrontendTiming("route_ready", 1150.5);
  window.dispatchEvent(new Event("load"));
  await new Promise((resolve) => window.setTimeout(resolve, 0));

  expect(
    log.mock.calls.every(([, metadata]) => typeof metadata === "string"),
  ).toBe(true);
  const records = log.mock.calls.map(([, metadata]) =>
    JSON.parse(metadata as string),
  );
  expect(records).toContainEqual({
    phase: "javascript_ready",
    durationMs: 1200,
  });
  expect(records).toContainEqual({
    phase: "auth_ready",
    durationMs: 100.13,
  });
  expect(records).toContainEqual({
    phase: "route_ready",
    durationMs: 1150.5,
  });
  expect(records).toContainEqual(
    expect.objectContaining({
      phase: "navigation",
      ttfbMs: 150,
      htmlDownloadMs: 50,
      domContentLoadedMs: 1000,
      loadMs: 1150,
    }),
  );
  expect(records).toContainEqual({
    phase: "asset",
    asset: "index-abc.js",
    durationMs: 750,
    queueMs: 25,
    headersMs: 100,
    bodyMs: 550,
    transferBytes: 1500,
    encodedBytes: 1200,
    decodedBytes: 6500,
    protocol: "h2",
  });
  expect(JSON.stringify(log.mock.calls)).not.toMatch(
    /secret|third-party|\/api\/|fund-screener/,
  );
  stop();
});

it("serializes buffered resource and paint observer records using the same safe metadata contract", async () => {
  window.history.replaceState(null, "", "/fund-screener?perf=1&token=secret");
  const log = vi.spyOn(console, "info").mockImplementation(() => {});
  const callbacks: Array<(entries: { getEntries: () => unknown[] }) => void> =
    [];
  const observer = Object.assign(
    vi.fn(function (
      callback: (entries: { getEntries: () => unknown[] }) => void,
    ) {
      callbacks.push(callback);
      return { observe: vi.fn(), disconnect: vi.fn() };
    }),
    { supportedEntryTypes: ["resource", "paint"] },
  );
  vi.stubGlobal("PerformanceObserver", observer);

  const stop = installFrontendPerformance();
  callbacks[0]({
    getEntries: () => [
      {
        name: `${window.location.origin}/assets/FundNavTrendChart-abc.js?token=secret#fragment`,
        duration: 246.789,
        fetchStart: 100,
        requestStart: 110.125,
        responseStart: 210.25,
        responseEnd: 346.789,
        transferSize: 300,
        encodedBodySize: 250,
        decodedBodySize: 1250,
        nextHopProtocol: "h3",
      },
      {
        name: `${window.location.origin}/private/assets/account.json?token=secret`,
      },
      { name: "https://external.test/assets/secret.js" },
    ],
  });
  callbacks[1]({
    getEntries: () => [{ name: "first-contentful-paint", startTime: 456.789 }],
  });
  await new Promise((resolve) => window.setTimeout(resolve, 0));

  expect(
    log.mock.calls.every(([, metadata]) => typeof metadata === "string"),
  ).toBe(true);
  const records = log.mock.calls.map(([, metadata]) =>
    JSON.parse(metadata as string),
  );
  expect(records.filter((record) => record.phase === "asset")).toEqual([
    {
      phase: "asset",
      asset: "FundNavTrendChart-abc.js",
      durationMs: 246.79,
      queueMs: 10.13,
      headersMs: 100.13,
      bodyMs: 136.54,
      transferBytes: 300,
      encodedBytes: 250,
      decodedBytes: 1250,
      protocol: "h3",
    },
  ]);
  expect(records).toContainEqual({
    phase: "first-contentful-paint",
    durationMs: 456.79,
  });
  expect(JSON.stringify(log.mock.calls)).not.toMatch(
    /secret|fragment|external\.test|\/private\//,
  );
  stop();
});

it("keeps cached or unavailable subphase timestamps nonnegative and preserves zero byte counts", async () => {
  window.history.replaceState(null, "", "/?perf=1");
  const log = vi.spyOn(console, "info").mockImplementation(() => {});
  vi.stubGlobal("PerformanceObserver", undefined);
  vi.stubGlobal("performance", {
    now: () => 1000,
    getEntriesByType: (type: string) =>
      type === "resource"
        ? [
            {
              name: `${window.location.origin}/assets/cached.css`,
              duration: 10,
              fetchStart: 100,
              requestStart: 0,
              responseStart: 0,
              responseEnd: 0,
              transferSize: 0,
              encodedBodySize: 0,
              decodedBodySize: 0,
              nextHopProtocol: "",
            },
          ]
        : [],
  });
  const stop = installFrontendPerformance();
  window.dispatchEvent(new Event("load"));
  await new Promise((resolve) => window.setTimeout(resolve, 0));

  expect(
    log.mock.calls.every(([, metadata]) => typeof metadata === "string"),
  ).toBe(true);
  const records = log.mock.calls.map(([, metadata]) =>
    JSON.parse(metadata as string),
  );
  expect(records).toContainEqual({
    phase: "asset",
    asset: "cached.css",
    durationMs: 10,
    queueMs: 0,
    headersMs: 0,
    bodyMs: 0,
    transferBytes: 0,
    encodedBytes: 0,
    decodedBytes: 0,
    protocol: "",
  });
  stop();
});

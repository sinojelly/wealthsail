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
              transferSize: 1500,
              decodedBodySize: 6500,
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
  window.dispatchEvent(new Event("load"));
  await new Promise((resolve) => window.setTimeout(resolve, 0));

  expect(log).toHaveBeenCalledWith("frontend_performance", {
    phase: "javascript_ready",
    durationMs: 1200,
  });
  expect(log).toHaveBeenCalledWith("frontend_performance", {
    phase: "auth_ready",
    durationMs: 100.13,
  });
  expect(log).toHaveBeenCalledWith(
    "frontend_performance",
    expect.objectContaining({
      phase: "navigation",
      ttfbMs: 150,
      htmlDownloadMs: 50,
    }),
  );
  expect(log).toHaveBeenCalledWith("frontend_performance", {
    phase: "asset",
    asset: "index-abc.js",
    durationMs: 750,
    transferBytes: 1500,
    decodedBytes: 6500,
  });
  expect(JSON.stringify(log.mock.calls)).not.toMatch(
    /secret|third-party|\/api\/|fund-screener/,
  );
  stop();
});

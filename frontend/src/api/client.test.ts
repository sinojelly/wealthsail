import { afterEach, expect, it, vi } from "vitest";
import { request, requestWithTimeout, setAccessToken } from "./client";

afterEach(() => {
  setAccessToken(null);
  window.history.replaceState(null, "", "/");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it.each(["headers", "body"])(
  "bounds a read stalled while awaiting %s and aborts its HTTP request",
  async (phase) => {
    vi.useFakeTimers();
    const stalled = new Promise<Response>(() => {});
    const fetchMock = vi.fn().mockImplementation(() =>
      phase === "headers"
        ? stalled
        : {
            text: () => new Promise<string>(() => {}),
          },
    );
    vi.stubGlobal("fetch", fetchMock);
    const read = requestWithTimeout(
      "/v1/investment/opportunity-context",
      {},
      30_000,
    );
    const rejected = expect(read).rejects.toMatchObject({
      status: 408,
      code: "REQUEST_TIMEOUT",
    });
    await vi.advanceTimersByTimeAsync(30_000);
    await rejected;
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  },
);

it("preserves caller cancellation and clears the read deadline", async () => {
  vi.useFakeTimers();
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise(() => {})),
  );
  const controller = new AbortController();
  const read = requestWithTimeout("/v1/investment/directions", {
    signal: controller.signal,
  });
  const rejected = expect(read).rejects.toMatchObject({ name: "AbortError" });
  controller.abort();
  await rejected;
  expect(vi.getTimerCount()).toBe(0);
});

it("reports opt-in API header, body, parsing and server timing without payloads or queries", async () => {
  window.history.replaceState(null, "", "/fund-screener?perf=1");
  const log = vi.spyOn(console, "info").mockImplementation(() => {});
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ data: { secret: "private-payload" }, meta: {} }),
        {
          status: 200,
          headers: {
            "X-Request-Id": "api-perf",
            "X-Request-Time-Ms": "120",
            "Server-Timing": "app;dur=120, db;dur=90",
          },
        },
      ),
    ),
  );
  await request("/v1/investment/opportunity-context?secret=private-query");
  expect(log).toHaveBeenCalledWith(
    "frontend_performance",
    expect.objectContaining({
      phase: "api",
      path: "/v1/investment/opportunity-context",
      status: 200,
      requestId: "api-perf",
      serverMs: 120,
      headersMs: expect.any(Number),
      bodyMs: expect.any(Number),
      parseMs: expect.any(Number),
      durationMs: expect.any(Number),
    }),
  );
  expect(JSON.stringify(log.mock.calls)).not.toMatch(
    /private-payload|private-query/,
  );
});

it("keeps request performance diagnostics off by default", async () => {
  const log = vi.spyOn(console, "info").mockImplementation(() => {});
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ data: {}, meta: {} }))),
  );
  await request("/v1/test");
  expect(log).not.toHaveBeenCalled();
});

it("adds the installed access token to authenticated API requests", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        data: { id: "user-1" },
        meta: { requestId: "req-1", asOf: "2026-08-28T00:00:00+08:00" },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  setAccessToken("signed-access-token");

  await request("/v1/auth/me");

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(new Headers(init.headers).get("Authorization")).toBe(
    "Bearer signed-access-token",
  );
  expect(init.credentials).toBe("include");
});

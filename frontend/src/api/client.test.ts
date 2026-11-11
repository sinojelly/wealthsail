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
  expect(log).toHaveBeenCalledWith("frontend_performance", expect.any(String));
  expect(JSON.parse(log.mock.calls[0][1])).toEqual(
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

it.each(["HEADERS", "BODY"] as const)(
  "records safe JSON timing when the %s read rejects",
  async (failurePhase) => {
    window.history.replaceState(null, "", "/fund-screener?perf=1");
    const log = vi.spyOn(console, "info").mockImplementation(() => {});
    const failure = new Error("private-provider-error");
    let clock = 0;
    vi.spyOn(performance, "now").mockImplementation(() => clock);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        if (failurePhase === "HEADERS") {
          clock = 1150;
          throw failure;
        }
        clock = 150;
        return {
          status: 200,
          headers: new Headers({
            "X-Request-Id": "timed-body",
            "X-Request-Time-Ms": "140",
          }),
          text: async () => {
            clock = 1150;
            throw failure;
          },
        };
      }),
    );
    setAccessToken("private-access-token");
    await expect(
      request("/v1/investment/opportunity-context?secret=private-query"),
    ).rejects.toBe(failure);
    expect(log).toHaveBeenCalledTimes(1);
    const timing = JSON.parse(log.mock.calls[0][1]);
    expect(timing).toMatchObject({
      phase: "api",
      path: "/v1/investment/opportunity-context",
      failurePhase,
      status: failurePhase === "BODY" ? 200 : 0,
      headersMs: failurePhase === "BODY" ? 150 : 1150,
      bodyMs: failurePhase === "BODY" ? 1000 : 0,
      parseMs: 0,
      durationMs: 1150,
    });
    expect(JSON.stringify(log.mock.calls)).not.toMatch(
      /private-provider-error|private-access-token|private-query/,
    );
  },
);

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

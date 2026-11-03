import { API_BASE_URL, HttpError } from "./client";
import {
  getResearchAcceptance,
  getResearchArtifacts,
  getResearchReplays,
  replayResearchAcceptance,
} from "./research-acceptance";

afterEach(() => vi.unstubAllGlobals());

it("reads the requested frozen run and its artifacts with the caller's abort signal", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(
      async () =>
        new Response(
          JSON.stringify({ data: { researchId: "a/b" }, meta: {} }),
          { status: 200 },
        ),
    );
  vi.stubGlobal("fetch", fetcher);
  const controller = new AbortController();
  expect(await getResearchAcceptance("a/b", controller.signal)).toEqual({
    researchId: "a/b",
  });
  await getResearchArtifacts("a/b", controller.signal);
  await getResearchReplays("a/b", controller.signal);
  const prefix = `${API_BASE_URL}/v1/investment/daily-research/validation/a%2Fb`;
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
    prefix,
    `${prefix}/artifacts`,
    `${prefix}/replays`,
  ]);
  for (const [, init] of fetcher.mock.calls) {
    expect(init.signal).toBe(controller.signal);
    expect(init.method).toBeUndefined();
  }
});

it("sends the exact replay idempotency key without dispatching another research endpoint", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ data: { id: "replay-1" }, meta: {} }), {
      status: 200,
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  const key = "7a9ea606-f2c7-401b-a876-2f6ab01cd245";
  expect(await replayResearchAcceptance("run-1", key)).toEqual({
    id: "replay-1",
  });
  expect(fetcher).toHaveBeenCalledOnce();
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe(
    `${API_BASE_URL}/v1/investment/daily-research/validation/run-1/replays`,
  );
  expect(init.method).toBe("POST");
  expect(JSON.parse(init.body)).toEqual({ commandKey: key });
});

it("preserves the missing-record status for a quiet legacy-run fallback", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: "NOT_FOUND", message: "not found" },
        }),
        { status: 404 },
      ),
    ),
  );
  await expect(getResearchAcceptance("old-run")).rejects.toEqual(
    expect.any(HttpError),
  );
});

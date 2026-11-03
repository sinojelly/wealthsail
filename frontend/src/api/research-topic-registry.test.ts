import { API_BASE_URL } from "./client";
import * as api from "./research-topic-registry";
afterEach(() => vi.unstubAllGlobals());
it("paginates saved registry snapshots using only encoded GET requests for allowed sizes", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(
      async () =>
        new Response(
          JSON.stringify({ data: { researchId: "run" }, meta: {} }),
          { status: 200 },
        ),
    );
  vi.stubGlobal("fetch", fetcher);
  const signal = new AbortController().signal;
  for (const limit of [12, 20, 30] as const)
    await api.getResearchTopicRegistry(
      "a/b",
      { limit, cursor: "opaque+/=" },
      signal,
    );
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual(
    [12, 20, 30].map(
      (limit) =>
        `${API_BASE_URL}/v1/investment/daily-research/topic-registry/a%2Fb?limit=${limit}&cursor=opaque%2B%2F%3D`,
    ),
  );
  for (const [, init] of fetcher.mock.calls) {
    expect(init.signal).toBe(signal);
    expect(init.method).toBeUndefined();
    expect(init.body).toBeUndefined();
  }
});

import { API_BASE_URL } from "./client";
import { getTopicFundRecommendations } from "./topic-fund-recommendations";

afterEach(() => vi.unstubAllGlobals());

it("reads only the encoded research recommendation route and forwards cancellation", async () => {
  const response = { researchId: "a/b", items: [], actionable: false };
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ data: response, meta: {} })),
    );
  vi.stubGlobal("fetch", fetcher);
  const signal = new AbortController().signal;
  expect(await getTopicFundRecommendations("a/b", signal)).toEqual(response);
  const [url, init] = fetcher.mock.calls[0];
  expect(url).toBe(
    `${API_BASE_URL}/v1/investment/daily-research/topic-fund-recommendations/a%2Fb`,
  );
  expect(init.signal).toBe(signal);
  expect(init.method).toBeUndefined();
  expect(init.body).toBeUndefined();
});

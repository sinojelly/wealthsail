import * as api from "./investment";
import { request } from "./client";
vi.mock("./client", () => ({ request: vi.fn() }));

beforeEach(() => {
  vi.mocked(request).mockResolvedValue({ data: {}, meta: {} } as never);
});

it("encodes the complete opportunity scope and forwards cancellation", async () => {
  const controller = new AbortController();
  await api.getOpportunityContext(
    {
      directionId: "a-share:broad",
      indexId: "hs300",
      instrumentId: "fund-id",
      fundCode: "510330",
      fundMarket: "SSE",
      months: 60,
    },
    controller.signal,
  );
  const [url, init] = vi.mocked(request).mock.calls.at(-1)!;
  const parsed = new URL(url, "https://app.test");
  expect(parsed.pathname).toBe("/v1/investment/opportunity-context");
  expect(Object.fromEntries(parsed.searchParams)).toEqual({
    directionId: "a-share:broad",
    indexId: "hs300",
    instrumentId: "fund-id",
    fundCode: "510330",
    fundMarket: "SSE",
    months: "60",
  });
  expect(init?.signal).toBe(controller.signal);
});

it("uses scoped JSON mutations for monitors and the notice acknowledgement endpoint", async () => {
  const scope = {
    directionId: "a-share:broad",
    indexId: "hs300",
    instrumentId: "fund-id",
    fundCode: "510330",
    fundMarket: "SSE",
  };
  await api.setConditionMonitor({ ...scope, enabled: true });
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/condition-monitors",
    { method: "POST", body: JSON.stringify({ ...scope, enabled: true }) },
  );
  await api.ackConditionNotice("notice/1");
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/condition-notices/notice%2F1/ack",
    { method: "POST" },
  );
});

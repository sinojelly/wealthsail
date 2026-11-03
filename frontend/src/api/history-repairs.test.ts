import { API_BASE_URL } from "./client";
import * as api from "./history-repairs";

afterEach(() => vi.unstubAllGlobals());

it("reads full administrator repair materials only through the separate detail GET", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(
      new Response(JSON.stringify({ data: { id: "repair" }, meta: {} }), {
        status: 200,
      }),
    );
  vi.stubGlobal("fetch", fetcher);
  const signal = new AbortController().signal;
  await api.getHistoricalRepairDetails("repair/a", signal);
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher).toHaveBeenCalledWith(
    `${API_BASE_URL}/v1/investment/daily-research/history-repairs/repair%2Fa/details`,
    expect.objectContaining({ signal }),
  );
  expect(fetcher.mock.calls[0][1].method).toBeUndefined();
});

it("keeps historical preview and list reads separate from explicit idempotent commands", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation(
      async () =>
        new Response(
          JSON.stringify({ data: { sourceRunId: "a/b" }, meta: {} }),
          { status: 200 },
        ),
    );
  vi.stubGlobal("fetch", fetcher);
  const signal = new AbortController().signal;
  await api.previewHistoricalRepair("a/b", signal);
  await api.listHistoricalRepairs("a/b", signal);
  await api.getHistoricalRepair("repair/a", signal);
  const key = "48dd5647-5c5b-42cf-9529-90cf588d806d";
  await api.generateHistoricalRepair("a/b", key);
  await api.reviewHistoricalRepair("repair/a", key, "APPROVE");
  await api.publishHistoricalRepair("repair/a", key);
  const prefix = `${API_BASE_URL}/v1/investment/daily-research/history-repairs`;
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
    `${prefix}/from/a%2Fb/preview`,
    `${prefix}?sourceRunId=a%2Fb`,
    `${prefix}/repair%2Fa`,
    `${prefix}/from/a%2Fb`,
    `${prefix}/repair%2Fa/reviews`,
    `${prefix}/repair%2Fa/publish`,
  ]);
  for (const [, options] of fetcher.mock.calls.slice(0, 3)) {
    expect(options.signal).toBe(signal);
    expect(options.method).toBeUndefined();
  }
  expect(
    fetcher.mock.calls.slice(3).map(([, options]) => JSON.parse(options.body)),
  ).toEqual([
    { commandKey: key },
    { commandKey: key, decision: "APPROVE" },
    { commandKey: key },
  ]);
});

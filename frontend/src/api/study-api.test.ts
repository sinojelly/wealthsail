import { request } from "./client";
import * as api from "./study-api";
vi.mock("./client", () => ({ request: vi.fn() }));
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(request).mockResolvedValue({
    data: { items: [], total: 0 },
    meta: {},
  } as never);
});
it("keeps study paths, abort signals and version guards separate from the old condition monitors", async () => {
  const signal = new AbortController().signal;
  await api.getStudyTracks(signal);
  expect(request).toHaveBeenLastCalledWith("/v1/investment/study-tracks", {
    signal,
  });
  await api.getStudyDetail("a/b", signal, 50);
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/study-tracks/a%2Fb?limit=50&offset=50",
    { signal },
  );
  await api.createStudyTrack({
    originType: "DAILY_EVENT",
    reportRunId: "ready",
    eventId: "event",
    horizonDays: 30,
  });
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/study-tracks",
    expect.objectContaining({
      method: "POST",
      headers: { "Idempotency-Key": expect.any(String) },
      body: JSON.stringify({
        originType: "DAILY_EVENT",
        reportRunId: "ready",
        eventId: "event",
        horizonDays: 30,
      }),
    }),
  );
  await api.checkStudy("track", 3);
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/study-tracks/track/check",
    expect.objectContaining({ body: '{"expectedGeneration":3}' }),
  );
  await api.setStudyStatus("track", { expectedGeneration: 3, enabled: false });
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/study-tracks/track/status",
    expect.objectContaining({
      body: '{"expectedGeneration":3,"enabled":false}',
    }),
  );
  await api.updateStudyVersion("track", {
    expectedGeneration: 3,
    reason: "修订",
  });
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/study-tracks/track/versions",
    expect.objectContaining({
      body: '{"expectedGeneration":3,"reason":"修订"}',
    }),
  );
  await api.addStudyNote("track", { expectedGeneration: 3, kind: "ACK" });
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/study-tracks/track/notes",
    expect.objectContaining({ body: '{"expectedGeneration":3,"kind":"ACK"}' }),
  );
  await api.getEligibleStudyTrades("track", signal);
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/study-tracks/track/eligible-trades",
    { signal },
  );
  await api.linkStudyTrade("track", {
    expectedGeneration: 3,
    fillId: "fill",
    reason: "实际成交",
  });
  expect(request).toHaveBeenLastCalledWith(
    "/v1/investment/study-tracks/track/trade-links",
    expect.objectContaining({
      body: '{"expectedGeneration":3,"fillId":"fill","reason":"实际成交"}',
    }),
  );
});

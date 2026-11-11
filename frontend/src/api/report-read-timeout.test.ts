import { afterEach, expect, it, vi } from "vitest";
import { getDirections, getIndices, getOpportunityContext } from "./investment";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it.each([
  ["directions", () => getDirections()],
  ["indices", () => getIndices("broad")],
  ["report", () => getOpportunityContext({ months: 36 })],
] as const)(
  "ends a stalled %s read after 30 seconds without retrying",
  async (_name, load) => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    const rejected = expect(load()).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
    });
    await vi.advanceTimersByTimeAsync(30_000);
    await rejected;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
  },
);

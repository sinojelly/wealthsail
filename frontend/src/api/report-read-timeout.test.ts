import { afterEach, expect, it, vi } from "vitest";
import { getDirections, getIndices, getOpportunityContext } from "./investment";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it.each([
  ["directions", () => getDirections(), 30_000],
  ["indices", () => getIndices("broad"), 30_000],
  ["report", () => getOpportunityContext({ months: 36 }), 90_000],
] as const)(
  "ends a stalled %s read at its own deadline without retrying",
  async (_name, load, timeoutMs) => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchMock);
    const rejected = expect(load()).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
    });
    await vi.advanceTimersByTimeAsync(timeoutMs - 1);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await rejected;
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
  },
);

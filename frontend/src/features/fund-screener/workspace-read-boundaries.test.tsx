import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FundScreenerPage from "./FundScreenerPage";
import { renderWithSession } from "../../test/render-with-session";
import { fundTrack } from "./personal-funds.test-data";

it("keeps refresh, four-workspace navigation and visible NAV reads free of research commands", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-07T04:00:00Z"));
  sessionStorage.clear();
  const observed: Array<{
    element: Element;
    enter: () => void;
  }> = [];
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      callback: IntersectionObserverCallback;
      constructor(callback: IntersectionObserverCallback) {
        this.callback = callback;
      }
      observe(element: Element) {
        observed.push({
          element,
          enter: () =>
            this.callback(
              [
                {
                  target: element,
                  isIntersecting: true,
                } as IntersectionObserverEntry,
              ],
              this as unknown as IntersectionObserver,
            ),
        });
      }
      disconnect() {}
    },
  );
  const unexpected: string[] = [];
  const reads: string[] = [];
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "https://example.test");
      const path = url.pathname.replace(/^\/api/, "");
      reads.push(`${init?.method ?? "GET"} ${path}${url.search}`);
      let data: unknown;
      if (path === "/v1/investment/directions" || path === "/v1/watchlist")
        data = [];
      else if (path === "/v1/investment/opportunity-context")
        data = { state: "UNKNOWN", items: [], events: [] };
      else if (path === "/v1/investment/daily-research/refresh")
        data = { enabled: true, request: null };
      else if (path === "/v1/investment/study-tracks")
        data = {
          items: [fundTrack("one", "991042"), fundTrack("two", "991043")],
          total: 2,
        };
      else if (path === "/v1/investment/personal-funds-reference")
        data = {
          version: "personal-funds-reference-v1",
          source: "LOCAL_DATABASE_ONLY",
          readOnly: true,
          asOf: "2026-11-07T12:00:00+08:00",
          funds: [],
        };
      else if (path === "/v1/funds/quotes")
        data = { asOf: "2026-11-07T04:00:00Z", items: [] };
      else if (path === "/v1/funds/991042/nav-history")
        data = {
          methodVersion: "fund-unit-nav-trend-v1",
          fundCode: "991042",
          fundName: "测试基金",
          period: url.searchParams.get("period"),
          state: "READY",
          priceBasis: "UNIT_NAV",
          unit: "CNY/份",
          asOf: "2026-11-06",
          retrievedAt: "2026-11-07T01:00:00Z",
          sourceUri: "https://example.test/nav",
          points: [
            { date: "2026-11-05", unitNav: "1.12" },
            { date: "2026-11-06", unitNav: "1.14" },
          ],
          coverage: {
            startDate: "2026-11-05",
            endDate: "2026-11-06",
            fullHistory: false,
            requestedStartDate: null,
            sampleCount: 2,
          },
          summary: {
            minimumNav: "1.12",
            maximumNav: "1.14",
            currentNav: "1.14",
            percentilePct: 100,
            percentileMethod: "sample",
          },
          refresh: { state: "IDLE", runId: null, pollAfterMs: null },
        };
      else {
        unexpected.push(`${init?.method ?? "GET"} ${path}`);
        throw new Error(`Unexpected read: ${path}`);
      }
      return new Response(JSON.stringify({ data, meta: {} }), { status: 200 });
    },
  );
  vi.stubGlobal("fetch", fetcher);
  const assertReadOnly = () => {
    expect(unexpected).toEqual([]);
    expect(
      fetcher.mock.calls.every(
        ([, init]) => !init?.method || init.method === "GET",
      ),
    ).toBe(true);
    expect(reads.some((url) => url.includes("/nav-history"))).toBe(true);
  };
  try {
    const page = renderWithSession(<FundScreenerPage />, "/fund-screener");
    await screen.findByRole("button", { name: "手动启动研究" });
    await userEvent.click(screen.getByRole("tab", { name: "经济事件" }));
    await userEvent.click(screen.getByRole("tab", { name: "我的基金" }));
    await screen.findByRole("article", { name: "基金991042" });
    expect(reads.filter((url) => url.includes("/nav-history"))).toEqual([]);
    await waitFor(() => expect(observed).toHaveLength(2));
    act(() =>
      observed
        .find(
          (row) =>
            row.element.getAttribute("aria-label") === "991042净值走势图区域",
        )!
        .enter(),
    );
    await screen.findByRole("img", { name: "991042单位净值走势" });
    expect(reads.filter((url) => url.includes("/nav-history"))).toHaveLength(1);
    expect(reads.some((url) => url.includes("991043/nav-history"))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "刷新我的基金" }));
    await userEvent.click(
      within(
        screen.getByRole("region", { name: "991042单位净值图" }),
      ).getByRole("button", { name: "近1年" }),
    );
    await waitFor(() =>
      expect(
        reads.some((url) => url.includes("991042/nav-history?period=1y")),
      ).toBe(true),
    );
    await userEvent.click(screen.getByRole("tab", { name: "市场证据" }));
    await userEvent.click(screen.getByRole("tab", { name: "今日重点" }));
    assertReadOnly();
    const before = reads.filter((url) =>
      url.includes("opportunity-context"),
    ).length;
    page.unmount();
    renderWithSession(<FundScreenerPage />, "/fund-screener");
    await waitFor(() =>
      expect(
        reads.filter((url) => url.includes("opportunity-context")),
      ).toHaveLength(before + 1),
    );
    await screen.findByRole("button", { name: "手动启动研究" });
    assertReadOnly();
  } finally {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  }
});

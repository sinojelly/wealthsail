import { act, screen } from "@testing-library/react";
import { onlineManager } from "@tanstack/react-query";
import type { DailyResearch, OpportunityContext } from "../../api/investment";
import * as api from "../../api/investment";
import FundScreenerPage from "./FundScreenerPage";
import { renderWithSession } from "../../test/render-with-session";

vi.mock("../../api/investment", () => ({
  getDirections: vi.fn().mockResolvedValue([]),
  getIndices: vi.fn().mockResolvedValue([]),
  getMarketContext: vi.fn(),
  getOpportunityContext: vi.fn(),
  screenFunds: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 }),
  getAnalysis: vi.fn(),
  searchResearch: vi.fn().mockResolvedValue([]),
  refreshResearch: vi.fn(),
  getRefreshStatus: vi.fn(),
}));
vi.mock("../../api/watchlist", () => ({
  list: vi.fn().mockResolvedValue([]),
  add: vi.fn(),
}));
vi.mock("./components/MacroContextStrip", () => ({
  default: ({ data }: { data?: OpportunityContext }) => (
    <output aria-label="economic-delivery">
      {data?.dailyResearch?.economicResearch?.status ?? "NONE"}
    </output>
  ),
}));
vi.mock("./components/TrackedFundWatch", () => ({ default: () => null }));
vi.mock("./components/StudyTracking", () => ({ default: () => null }));
vi.mock("./components/DailyResearchRefresh", () => ({ default: () => null }));
vi.mock("./components/GlobalResearchSearch", () => ({ default: () => null }));
vi.mock("./components/FundResultsTable", () => ({ default: () => null }));

const daily = (
  status: "PENDING" | "PARTIAL" | "AVAILABLE" = "PENDING",
): DailyResearch => ({
  methodVersion: "daily-research-v1",
  state: "FAILED",
  runId: "current-run",
  report: null,
  scheduledFor: null,
  startedAt: null,
  completedAt: null,
  asOf: null,
  nextRunAt: null,
  economicResearch: {
    version: "economic-research-delivery-v1",
    researchId: "current-run",
    status,
    asOf: null,
    validatedAt: null,
    recordedAt: null,
    archiveState: status === "PENDING" ? "ARCHIVING" : "ARCHIVED",
    archivePending: status === "PENDING",
    observations: {
      sources: [],
      industryCycleReviews: [],
      valueDirections: [],
      events: [],
      upcoming: [],
    },
    acceptedContentComplete: true,
    actionable: false,
  },
});
const context = (
  status: "PENDING" | "PARTIAL" | "AVAILABLE",
): OpportunityContext => ({
  state: "UNKNOWN",
  items: [],
  dailyResearch: daily(status),
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getMarketContext).mockReset();
  vi.mocked(api.getOpportunityContext).mockReset();
  onlineManager.setOnline(true);
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

it.each(["PARTIAL", "AVAILABLE"] as const)(
  "refreshes both real context queries from archived-pending to %s and stops after archival",
  async (status) => {
    vi.mocked(api.getMarketContext)
      .mockResolvedValueOnce(context("PENDING"))
      .mockResolvedValue(context(status));
    vi.mocked(api.getOpportunityContext)
      .mockResolvedValueOnce(context("PENDING"))
      .mockResolvedValue(context(status));
    renderWithSession(<FundScreenerPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20);
    });
    expect(screen.getByLabelText("economic-delivery")).toHaveTextContent(
      "PENDING",
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_020);
    });
    expect(screen.getByLabelText("economic-delivery")).toHaveTextContent(
      status,
    );
    expect(api.getMarketContext).toHaveBeenCalledTimes(2);
    expect(api.getOpportunityContext).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120_000);
    });
    expect(api.getMarketContext).toHaveBeenCalledTimes(2);
    expect(api.getOpportunityContext).toHaveBeenCalledTimes(2);
    expect(api.refreshResearch).not.toHaveBeenCalled();
  },
);

it("slows pending GETs after five minutes and refuses terminal, mismatched, missing-ID or errored polling", async () => {
  const { economicArchivePollInterval } =
    await import("./economic-archive-polling");
  expect(economicArchivePollInterval(daily(), 0)).toBe(15_000);
  expect(economicArchivePollInterval(daily(), 299_999)).toBe(15_000);
  expect(economicArchivePollInterval(daily(), 300_000)).toBe(60_000);
  expect(economicArchivePollInterval(daily("PARTIAL"), 0)).toBe(false);
  const unavailable = daily();
  unavailable.economicResearch!.archiveState = "UNRECOVERABLE";
  expect(economicArchivePollInterval(unavailable, 0)).toBe(false);
  const mismatch = daily();
  mismatch.economicResearch!.researchId = "old-run";
  expect(economicArchivePollInterval(mismatch, 0)).toBe(false);
  const missing = daily();
  missing.runId = null;
  missing.economicResearch!.researchId = null;
  expect(economicArchivePollInterval(missing, 0)).toBe(false);
  expect(economicArchivePollInterval(daily(), 0, true)).toBe(false);
});

it("stops after three delayed recoveries following final context errors without claiming available research", async () => {
  vi.mocked(api.getMarketContext)
    .mockResolvedValueOnce(context("PENDING"))
    .mockRejectedValue(new TypeError("offline"));
  vi.mocked(api.getOpportunityContext)
    .mockResolvedValueOnce(context("PENDING"))
    .mockRejectedValue(new TypeError("offline"));
  renderWithSession(<FundScreenerPage />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_050);
  });
  expect(api.getMarketContext).toHaveBeenCalledTimes(2);
  expect(api.getOpportunityContext).toHaveBeenCalledTimes(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(600_000);
  });
  expect(api.getMarketContext).toHaveBeenCalledTimes(5);
  expect(api.getOpportunityContext).toHaveBeenCalledTimes(5);
  expect(screen.getByLabelText("economic-delivery")).not.toHaveTextContent(
    "AVAILABLE",
  );
  expect(api.refreshResearch).not.toHaveBeenCalled();
});

it("recovers from a final temporary GET error after a bounded delay and stops once the archive is available", async () => {
  for (const request of [api.getMarketContext, api.getOpportunityContext]) {
    vi.mocked(request)
      .mockResolvedValueOnce(context("PENDING"))
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValue(context("PARTIAL"));
  }
  renderWithSession(<FundScreenerPage />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_050);
  });
  expect(screen.getByLabelText("economic-delivery")).toHaveTextContent(
    "PENDING",
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_050);
  });
  expect(screen.getByLabelText("economic-delivery")).toHaveTextContent(
    "PARTIAL",
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300_000);
  });
  expect(api.getMarketContext).toHaveBeenCalledTimes(3);
  expect(api.getOpportunityContext).toHaveBeenCalledTimes(3);
  expect(api.refreshResearch).not.toHaveBeenCalled();
});

it("keeps the recovery allowance spent after successful pending responses for the same run", async () => {
  for (const request of [api.getMarketContext, api.getOpportunityContext]) {
    vi.mocked(request)
      .mockResolvedValueOnce(context("PENDING"))
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(context("PENDING"))
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(context("PENDING"))
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(context("PENDING"))
      .mockRejectedValue(new TypeError("offline"));
  }
  renderWithSession(<FundScreenerPage />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(900_000);
  });
  expect(api.getMarketContext).toHaveBeenCalledTimes(8);
  expect(api.getOpportunityContext).toHaveBeenCalledTimes(8);
  expect(screen.getByLabelText("economic-delivery")).not.toHaveTextContent(
    "AVAILABLE",
  );
  expect(api.refreshResearch).not.toHaveBeenCalled();
});

it("permits only one controlled reconnection check for the same pending run without starting research", async () => {
  vi.mocked(api.getMarketContext)
    .mockResolvedValueOnce(context("PENDING"))
    .mockRejectedValue(new TypeError("offline"));
  vi.mocked(api.getOpportunityContext)
    .mockResolvedValueOnce(context("PENDING"))
    .mockRejectedValue(new TypeError("offline"));
  renderWithSession(<FundScreenerPage />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15_050);
  });
  await act(async () => {
    onlineManager.setOnline(false);
    onlineManager.setOnline(true);
    await vi.advanceTimersByTimeAsync(20);
  });
  expect(api.getMarketContext).toHaveBeenCalledTimes(3);
  expect(api.getOpportunityContext).toHaveBeenCalledTimes(3);
  await act(async () => {
    onlineManager.setOnline(false);
    onlineManager.setOnline(true);
    await vi.advanceTimersByTimeAsync(20);
  });
  expect(api.getMarketContext).toHaveBeenCalledTimes(3);
  expect(api.getOpportunityContext).toHaveBeenCalledTimes(3);
  expect(api.refreshResearch).not.toHaveBeenCalled();
});

it("preserves ordinary context reconnection refreshes when there is no economic archive wait", async () => {
  const ordinary = { state: "UNKNOWN", items: [] };
  vi.mocked(api.getMarketContext).mockResolvedValue(ordinary);
  vi.mocked(api.getOpportunityContext).mockResolvedValue(ordinary);
  renderWithSession(<FundScreenerPage />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20);
  });
  for (let check = 0; check < 2; check++) {
    await act(async () => {
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
      await vi.advanceTimersByTimeAsync(20);
    });
  }
  expect(api.getMarketContext).toHaveBeenCalledTimes(3);
  expect(api.getOpportunityContext).toHaveBeenCalledTimes(3);
  expect(api.refreshResearch).not.toHaveBeenCalled();
});

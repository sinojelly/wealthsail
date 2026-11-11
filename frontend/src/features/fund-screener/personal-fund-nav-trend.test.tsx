import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FundNavTrendChart from "./components/FundNavTrendChart";
import { useFundNavHistory } from "./nav-history-resource";

vi.mock("./nav-history-resource", () => ({ useFundNavHistory: vi.fn() }));

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    width: 600,
    height: 220,
    top: 0,
    right: 600,
    bottom: 220,
    left: 0,
    toJSON: () => ({}),
  });
  vi.mocked(useFundNavHistory).mockImplementation((_code, period) => {
    const date = period === "6m" ? "2026-11-06" : "2026-11-05";
    return {
      loading: false,
      failed: false,
      exhausted: false,
      retry: vi.fn(),
      data: {
        methodVersion: "fund-unit-nav-trend-v1",
        fundCode: "024194",
        fundName: "真实基金",
        period,
        priceBasis: "UNIT_NAV",
        unit: "CNY/份",
        state: "READY",
        asOf: "2026-11-07",
        retrievedAt: null,
        sourceUri: "https://example.org/unit-nav-history",
        points: [
          { date: "2026-11-04", unitNav: "1.0100" },
          { date, unitNav: "1.0800" },
        ],
        coverage: {
          startDate: "2026-11-04",
          endDate: date,
          fullHistory: false,
          requestedStartDate: null,
          sampleCount: 2,
        },
        summary: {
          minimumNav: "1.0100",
          maximumNav: "1.0800",
          currentNav: "1.0800",
          percentilePct: 100,
          percentileMethod: "observed-range",
        },
        refresh: { state: "IDLE", runId: null, pollAfterMs: null },
      },
    };
  });
});
afterEach(() => vi.restoreAllMocks());

it("warns using the real selected-period last point when the confirmed NAV is newer", async () => {
  const user = userEvent.setup();
  render(<FundNavTrendChart fundCode="024194" confirmedNavAsOf="2026-11-06" />);
  await screen.findByText(/曲线末点早于最新确认净值/);
  expect(screen.getByRole("status")).toHaveTextContent(
    "曲线末点早于最新确认净值",
  );
  expect(screen.getByRole("status")).toHaveTextContent("2026年11月5日");
  expect(screen.getByRole("status")).toHaveTextContent("2026年11月6日");
  expect(screen.getByText(/图中最新净值/)).toHaveTextContent("1.0800");
  await user.click(screen.getByRole("button", { name: "近半年" }));
  expect(
    screen.queryByText(/曲线末点早于最新确认净值/),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/图中最新净值/)).toHaveTextContent("2026年11月6日");
});

it("does not claim a date gap when confirmed NAV and real curve end on the same day", async () => {
  render(
    <FundNavTrendChart
      fundCode="024194"
      confirmedNavAsOf="2026-11-05T10:00:00Z"
    />,
  );
  expect(await screen.findByText(/图中最新净值/)).toHaveTextContent(
    "2026年11月5日",
  );
  expect(
    screen.queryByText(/曲线末点早于最新确认净值/),
  ).not.toBeInTheDocument();
});

it("keeps the actual curve source visible with its real observation date", async () => {
  render(<FundNavTrendChart fundCode="024194" />);
  expect(
    await screen.findByRole("link", { name: "曲线数据来源" }),
  ).toHaveAttribute("href", "https://example.org/unit-nav-history");
  expect(screen.getByText(/图中最新净值/)).toHaveTextContent("2026年11月5日");
});

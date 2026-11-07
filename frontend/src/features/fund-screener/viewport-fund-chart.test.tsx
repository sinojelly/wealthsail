import { act, render, screen } from "@testing-library/react";
import ViewportFundChart from "./components/ViewportFundChart";
import type { ActionableZoneTrack } from "../../api/investment";
import { frozenPlan } from "./personal-funds.test-data";

vi.mock("./components/FundNavTrendChart", () => ({
  default: ({
    fundCode,
    track,
    confirmedNavAsOf,
  }: {
    fundCode: string;
    track?: ActionableZoneTrack;
    confirmedNavAsOf?: string;
  }) => (
    <div
      role="img"
      aria-label={`${fundCode}单位净值走势`}
      data-has-price-plan={track !== undefined}
      data-entry-min={track?.entryZoneMin}
      data-confirmed-nav-as-of={confirmedNavAsOf}
    />
  ),
}));

type ObservedChart = {
  callback: IntersectionObserverCallback;
  observe: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
};

let observations: ObservedChart[];

function intersect(index: number, isIntersecting = true) {
  const observation = observations[index];
  act(() => {
    observation.callback(
      [{ isIntersecting } as IntersectionObserverEntry],
      observation as unknown as IntersectionObserver,
    );
  });
}

beforeEach(() => {
  observations = [];
  vi.stubGlobal(
    "IntersectionObserver",
    vi.fn(function (callback: IntersectionObserverCallback) {
      const observer = {
        callback,
        observe: vi.fn(),
        disconnect: vi.fn(),
      };
      observations.push(observer);
      return observer;
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

it("keeps the chart slot visible without requesting a chart before it approaches the viewport", () => {
  const { container } = render(<ViewportFundChart fundCode="000001" />);

  expect(screen.getByText(/正在加载单位净值走势/)).toBeVisible();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(observations[0].observe).toHaveBeenCalledWith(
    container.querySelector(".op-match-chart"),
  );

  intersect(0, false);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

it("automatically loads the correct fund chart without inventing a price plan when it approaches", () => {
  render(<ViewportFundChart fundCode="000001" />);

  intersect(0);

  expect(
    screen.getByRole("img", { name: "000001单位净值走势" }),
  ).toHaveAttribute("data-has-price-plan", "false");
  expect(screen.queryByText(/正在加载单位净值走势/)).not.toBeInTheDocument();
  expect(observations[0].disconnect).toHaveBeenCalled();
});

it("preserves the mounted chart after scrolling away and rerendering the same fund", () => {
  const { rerender } = render(<ViewportFundChart fundCode="000001" />);
  intersect(0);
  const chart = screen.getByRole("img", { name: "000001单位净值走势" });

  intersect(0, false);
  rerender(<ViewportFundChart fundCode="000001" />);

  expect(screen.getByRole("img", { name: "000001单位净值走势" })).toBe(chart);
  expect(observations).toHaveLength(1);
});

it("passes the selected frozen track and confirmed NAV date through activation and later updates", () => {
  const first = frozenPlan("000001").tracks[0];
  const second = frozenPlan("000001", "1.0800").tracks[0];
  const { rerender } = render(
    <ViewportFundChart
      fundCode="000001"
      track={first}
      confirmedNavAsOf="2026-11-06"
    />,
  );
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  intersect(0);
  const chart = screen.getByRole("img");
  expect(chart).toHaveAttribute("data-entry-min", first.entryZoneMin);
  expect(chart).toHaveAttribute("data-confirmed-nav-as-of", "2026-11-06");

  rerender(
    <ViewportFundChart
      fundCode="000001"
      track={second}
      confirmedNavAsOf="2026-11-07"
    />,
  );
  expect(screen.getByRole("img")).toBe(chart);
  expect(chart).toHaveAttribute("data-entry-min", second.entryZoneMin);
  expect(chart).toHaveAttribute("data-confirmed-nav-as-of", "2026-11-07");
});

it("disconnects a pending observer on unmount", () => {
  const { unmount } = render(<ViewportFundChart fundCode="000001" />);

  unmount();

  expect(observations[0].disconnect).toHaveBeenCalled();
});

it("loads the chart directly in browsers without IntersectionObserver", () => {
  vi.stubGlobal("IntersectionObserver", undefined);
  render(<ViewportFundChart fundCode="000001" />);

  expect(
    screen.getByRole("img", { name: "000001单位净值走势" }),
  ).toBeInTheDocument();
});

it("waits for the replacement fund to approach instead of reusing the previous chart activation", () => {
  const { rerender } = render(<ViewportFundChart fundCode="000001" />);
  intersect(0);

  rerender(<ViewportFundChart fundCode="000002" />);

  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.getByText(/正在加载单位净值走势/)).toBeVisible();
  intersect(0);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  intersect(1);
  expect(
    screen.getByRole("img", { name: "000002单位净值走势" }),
  ).toBeInTheDocument();
});

it("does not mount charts inside a collapsed section until the section is exposed", () => {
  const { container } = render(
    <details>
      <summary>本次研究基金观察</summary>
      <ViewportFundChart fundCode="000001" />
    </details>,
  );

  intersect(0, false);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();

  container.querySelector("details")!.open = true;
  intersect(0);
  expect(screen.getByRole("img", { name: "000001单位净值走势" })).toBeVisible();
});

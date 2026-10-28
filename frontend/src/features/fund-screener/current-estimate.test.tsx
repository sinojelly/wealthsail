import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import * as study from "../../api/study-api";
import type { StudyPriceWatch } from "../../api/study-api";
import PriceWatchSummary from "./components/PriceWatchSummary";
import TrackedFundWatch from "./components/TrackedFundWatch";

vi.mock("../../api/study-api", () => ({
  getStudyTracks: vi.fn(),
  setFundWatchStatus: vi.fn(),
  setStudyStatus: vi.fn(),
}));
beforeEach(() => vi.clearAllMocks());

const watch = (): StudyPriceWatch => ({
  frozenPlan: null,
  confirmedNav: null,
  estimate1430: {
    value: "1.0100",
    asOf: "2026-10-28T14:30:00+08:00",
    usable: true,
  },
  currentEstimate: {
    value: "1.0234",
    asOf: "2026-10-28T14:42:00+08:00",
    checkedAt: "2026-10-28T14:43:00+08:00",
    sourceUri: "https://example.com/current-fund-estimate",
    usable: true,
  },
  holding: { state: "NONE" },
  state: "OBSERVING",
  basis: "ESTIMATE_INTRADAY",
  asOf: "2026-10-28T14:42:00+08:00",
  reason: "按当前估算净值核对冻结参考线。",
});

it("shows the current fund estimate with its source time instead of the frozen 14:30 value", () => {
  render(<PriceWatchSummary watch={watch()} />);
  const region = screen.getByRole("region");
  expect(region).toHaveTextContent(
    "基金估算净值：1.0234（2026年10月28日 14:42）",
  );
  expect(region).not.toHaveTextContent("1.0100");
  expect(region).not.toHaveTextContent("14:30估算净值");
  expect(region).toHaveTextContent("当前判断：盘中估算净值（非最终净值）");
  expect(screen.getByRole("link", { name: "来源" })).toHaveAttribute(
    "href",
    "https://example.com/current-fund-estimate",
  );
});

it("shows an explicit unavailable placeholder when the current provider has no estimate", () => {
  render(
    <PriceWatchSummary
      watch={{ ...watch(), currentEstimate: null, basis: null }}
    />,
  );
  expect(screen.getByRole("region")).toHaveTextContent(
    "基金估算净值：----（时间未确认）",
  );
  expect(screen.getByRole("region")).not.toHaveTextContent("1.0100");
});

it("retains a dated legacy estimate for older API responses without claiming it is current", () => {
  const legacy = watch();
  delete legacy.currentEstimate;
  render(<PriceWatchSummary watch={legacy} />);
  expect(screen.getByRole("region")).toHaveTextContent(
    "基金估算净值：1.0100（2026年10月28日 14:30）",
  );
});

it("refreshes tracked quotes on each page entry even when cached study data is fresh", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(["study", "actor", "list"], { items: [], total: 0 });
  vi.mocked(study.getStudyTracks).mockResolvedValue({ items: [], total: 0 });
  function Page({ show }: { show: boolean }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter>
          {show && <TrackedFundWatch actor="actor" />}
        </MemoryRouter>
      </QueryClientProvider>
    );
  }
  const { rerender } = render(<Page show />);
  await waitFor(() => expect(study.getStudyTracks).toHaveBeenCalledTimes(1));
  rerender(<Page show={false} />);
  rerender(<Page show />);
  await waitFor(() => expect(study.getStudyTracks).toHaveBeenCalledTimes(2));
  client.clear();
});

const holding = {
  fundCode: "004069",
  fundMarket: "FUND_CN",
  instrumentId: "held-id",
  label: "南方证券ETF联接A",
  confirmedNav: { value: "1.0876", asOf: "2026-10-27", usable: true },
  currentEstimate: { ...watch().currentEstimate!, changePct: "-1.25" },
};
const manual = {
  id: "study",
  title: "证券研究",
  enabled: true,
  generation: 1,
  status: "OBSERVING",
  createdAt: "2026-10-20",
  updatedAt: "2026-10-28",
  unreadCount: 0,
  scope: { fundCode: "004069", fundMarket: "FUND_CN" },
  priceWatch: watch(),
};
function table() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <TrackedFundWatch actor="actor" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
it("merges imported holdings and manual studies into one fund row with colored estimated change", async () => {
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [manual],
    total: 1,
    heldFunds: [holding],
  });
  table();
  const grid = await screen.findByRole("table", { name: "持续跟踪与持仓基金" });
  await within(grid).findByText("004069");
  expect(within(grid).getAllByRole("row")).toHaveLength(2);
  expect(grid).toHaveTextContent("手动跟踪");
  expect(grid).toHaveTextContent("持仓基金");
  expect(within(grid).getByLabelText("估算涨跌幅")).toHaveTextContent("-1.25%");
  expect(within(grid).getByLabelText("估算涨跌幅")).toHaveClass("negative");
});

it("shows holdings without requiring a frozen research plan and excludes cancelled funds", async () => {
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [],
    total: 0,
    heldFunds: [
      holding,
      { ...holding, fundCode: "012733", label: "人工智能联接A" },
    ],
    suppressedFundKeys: ["FUND_CN:004069"],
  });
  table();
  const grid = await screen.findByRole("table", { name: "持续跟踪与持仓基金" });
  await within(grid).findByText("012733");
  expect(grid).not.toHaveTextContent("004069");
  expect(grid).toHaveTextContent("尚未保存买卖计划");
});

it("cancels a held fund persistently without deleting its holding and hides it after refetch", async () => {
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [],
    total: 0,
    heldFunds: [holding],
  });
  vi.mocked(study.setFundWatchStatus).mockImplementation(async () => {
    vi.mocked(study.getStudyTracks).mockResolvedValue({
      items: [],
      total: 0,
      heldFunds: [holding],
      suppressedFundKeys: ["FUND_CN:004069"],
    });
    return { enabled: false };
  });
  table();
  await userEvent.click(
    await screen.findByRole("button", { name: "取消跟踪 004069" }),
  );
  await waitFor(() =>
    expect(screen.queryByText("004069")).not.toBeInTheDocument(),
  );
  expect(study.setFundWatchStatus).toHaveBeenCalledWith({
    fundCode: "004069",
    fundMarket: "FUND_CN",
    enabled: false,
  });
});

it("keeps a fund visible with a retryable error if cancellation fails", async () => {
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [],
    total: 0,
    heldFunds: [holding],
  });
  vi.mocked(study.setFundWatchStatus).mockRejectedValue(
    new Error("取消失败，请重试"),
  );
  table();
  await userEvent.click(
    await screen.findByRole("button", { name: "取消跟踪 004069" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "取消失败，请重试",
  );
  expect(screen.getByRole("button", { name: "取消跟踪 004069" })).toBeEnabled();
});

it("shows a planless manual fund's current estimate without claiming frozen prices", async () => {
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [
      {
        ...manual,
        priceWatch: null,
        currentEstimate: { ...holding.currentEstimate, changePct: "1.25" },
      },
    ],
    total: 1,
  });
  table();
  const grid = await screen.findByRole("table", { name: "持续跟踪与持仓基金" });
  expect(await within(grid).findByLabelText("估算涨跌幅")).toHaveTextContent(
    "+1.25%",
  );
  expect(within(grid).getByLabelText("估算涨跌幅")).toHaveClass("positive");
  expect(grid).toHaveTextContent("行情与冻结价位待核对");
});

it("keeps and cancels an imported fund lacking a ticker by instrument identity", async () => {
  vi.mocked(study.getStudyTracks).mockResolvedValue({
    items: [],
    total: 0,
    heldFunds: [{ ...holding, fundCode: null }],
  });
  vi.mocked(study.setFundWatchStatus).mockImplementation(async () => {
    vi.mocked(study.getStudyTracks).mockResolvedValue({
      items: [],
      total: 0,
      heldFunds: [{ ...holding, fundCode: null }],
      suppressedFundKeys: ["FUND_CN:instrument:held-id"],
    });
    return { enabled: false };
  });
  table();
  await userEvent.click(
    await screen.findByRole("button", { name: "取消跟踪 南方证券ETF联接A" }),
  );
  await waitFor(() =>
    expect(screen.queryByText("南方证券ETF联接A")).not.toBeInTheDocument(),
  );
  expect(study.setFundWatchStatus).toHaveBeenCalledWith({
    instrumentId: "held-id",
    fundMarket: "FUND_CN",
    enabled: false,
  });
});

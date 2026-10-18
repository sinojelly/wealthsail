import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useNavigate } from "react-router-dom";
import FundScreenerPage from "./FundScreenerPage";
import { renderWithSession } from "../../test/render-with-session";
import * as study from "../../api/study-api";
vi.mock("../../api/study-api", () => ({
  getStudyTracks: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  getStudyDetail: vi.fn().mockRejectedValue(new Error("详情不可用")),
  createStudyTrack: vi.fn(),
  updateStudyVersion: vi.fn(),
  setStudyStatus: vi.fn(),
  checkStudy: vi.fn(),
  addStudyNote: vi.fn(),
  getEligibleStudyTrades: vi.fn(),
  linkStudyTrade: vi.fn(),
}));
const opportunity = vi.hoisted(() => vi.fn());
vi.mock("../../api/investment", () => ({
  getDirections: vi.fn().mockResolvedValue([
    {
      id: "a-share:broad",
      marketId: "a-share",
      marketLabel: "A股",
      styleId: "broad",
      label: "宽基",
      framework: "独立研究",
    },
  ]),
  getIndices: vi
    .fn()
    .mockResolvedValue([{ id: "hs300", code: "000300", name: "沪深300" }]),
  getMarketContext: vi.fn().mockResolvedValue({ state: "UNKNOWN", items: [] }),
  getOpportunityContext: opportunity,
  getConditionMonitors: vi.fn().mockResolvedValue([]),
  setConditionMonitor: vi.fn(),
  ackConditionNotice: vi.fn(),
  screenFunds: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 }),
  getAnalysis: vi.fn().mockResolvedValue({
    state: "UNKNOWN",
    history: [],
    source: { kind: "INDEX_PRICE" },
    indicators: {},
  }),
  searchResearch: vi.fn().mockResolvedValue([]),
  refreshResearch: vi.fn(),
  getRefreshStatus: vi.fn(),
}));
vi.mock("../../api/watchlist", () => ({
  list: vi.fn().mockResolvedValue([]),
  add: vi.fn(),
}));
beforeEach(() => {
  vi.clearAllMocks();
  opportunity.mockResolvedValue({
    state: "UNKNOWN",
    items: [],
    dailyResearch: {
      state: "FAILED",
      runId: "failed-latest",
      reportRunId: "ready-old",
      reportCompletedAt: "2026-10-16T01:00:00Z",
      report: {
        asOf: "2026-10-16T01:00:00Z",
        summary: "旧有效报告",
        events: [
          {
            id: "global-energy",
            title: "全球能源事件",
            summary: "全球线索",
            phase: "REPORTED",
            sourceIds: [],
            channels: [],
            scenarios: [],
            watchFor: [],
            unknowns: [],
            affectedDirectionIds: [],
          },
        ],
        upcoming: [],
        sources: [],
        coverage: [],
        energy: [],
      },
    },
  });
});
it("confirms a current selection before saving its full identity and chosen horizon", async () => {
  vi.mocked(study.createStudyTrack).mockResolvedValue({
    track: { id: "saved" },
  } as never);
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300&instrument=fund-1&fund=510330&fundMarket=SSE&macroMonths=60",
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "跟踪当前标的研究" }),
  );
  expect(study.createStudyTrack).not.toHaveBeenCalled();
  const horizon = screen.getByLabelText("观察天数");
  expect(horizon).toHaveValue(30);
  await userEvent.clear(horizon);
  await userEvent.type(horizon, "90");
  await userEvent.click(screen.getByRole("button", { name: "确认并保存跟踪" }));
  await waitFor(() =>
    expect(study.createStudyTrack).toHaveBeenCalledWith({
      originType: "SELECTION",
      scope: {
        directionId: "a-share:broad",
        indexId: "hs300",
        instrumentId: "fund-1",
        fundCode: "510330",
        fundMarket: "SSE",
      },
      horizonDays: 90,
    }),
  );
  await waitFor(() =>
    expect(screen.getByRole("tab", { name: "我的跟踪与复盘" })).toHaveAttribute(
      "aria-selected",
      "true",
    ),
  );
});
it("shows saved historical news without offering single-news tracking", async () => {
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300&researchTab=events",
  );
  expect(
    await within(
      await screen.findByRole("list", { name: "重要事件" }),
    ).findByRole("heading", { name: "全球能源事件" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /持续跟踪全球能源事件/ }),
  ).not.toBeInTheDocument();
  expect(study.createStudyTrack).not.toHaveBeenCalled();
});

function Navigate() {
  const navigate = useNavigate();
  return (
    <button
      onClick={() =>
        navigate(
          "/fund-screener?direction=a-share:broad&index=hs300&fund=510500&instrument=fund-2&fundMarket=SSE",
        )
      }
    >
      更换标的
    </button>
  );
}
it("does not navigate a different scope after a late successful create", async () => {
  let finish!: (value: never) => void;
  vi.mocked(study.createStudyTrack).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  renderWithSession(
    <>
      <FundScreenerPage />
      <Navigate />
    </>,
    "/fund-screener?direction=a-share:broad&index=hs300&instrument=fund-1&fund=510330&fundMarket=SSE",
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "跟踪当前标的研究" }),
  );
  await user.click(screen.getByRole("button", { name: "确认并保存跟踪" }));
  await user.click(screen.getByRole("button", { name: "更换标的" }));
  await act(async () => {
    finish({ track: { id: "old-saved" } } as never);
  });
  expect(screen.getByRole("tab", { name: "我的跟踪与复盘" })).toHaveAttribute(
    "aria-selected",
    "false",
  );
  expect(screen.getByRole("button", { name: /今日投资观察/ })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
});

it("cancels a source confirmation without persistence or research refresh", async () => {
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300",
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "跟踪当前标的研究" }),
  );
  expect(study.createStudyTrack).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "取消跟踪确认" }));
  expect(
    screen.queryByRole("region", { name: "确认研究假设" }),
  ).not.toBeInTheDocument();
  expect(study.createStudyTrack).not.toHaveBeenCalled();
});

it("clears an unsaved confirmation when the exact fund scope changes", async () => {
  renderWithSession(
    <>
      <FundScreenerPage />
      <Navigate />
    </>,
    "/fund-screener?direction=a-share:broad&index=hs300&fund=510330&instrument=fund-1&fundMarket=SSE",
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "跟踪当前标的研究" }),
  );
  expect(study.createStudyTrack).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "更换标的" }));
  expect(
    screen.queryByRole("region", { name: "确认研究假设" }),
  ).not.toBeInTheDocument();
  expect(study.createStudyTrack).not.toHaveBeenCalled();
});

it("previews the server direction hypothesis verbatim when provided and does not send rewritten text", async () => {
  const context = await opportunity();
  opportunity.mockResolvedValue({
    ...context,
    directionModel: {
      summary: "明确的原始标的假设：先验证盈利改善。",
      supports: [],
      counterFactors: [],
      missingEvidence: ["盈利证据"],
    },
  });
  vi.mocked(study.createStudyTrack).mockRejectedValue(
    new Error("保存失败可重试"),
  );
  renderWithSession(
    <FundScreenerPage />,
    "/fund-screener?direction=a-share:broad&index=hs300",
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "跟踪当前标的研究" }),
  );
  expect(
    screen.getByRole("region", { name: "确认研究假设" }),
  ).toHaveTextContent("明确的原始标的假设：先验证盈利改善。");
  expect(study.createStudyTrack).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "确认并保存跟踪" }));
  await waitFor(() =>
    expect(study.createStudyTrack).toHaveBeenCalledWith({
      originType: "SELECTION",
      scope: {
        directionId: "a-share:broad",
        indexId: "hs300",
        instrumentId: undefined,
        fundCode: undefined,
        fundMarket: undefined,
      },
      horizonDays: 30,
    }),
  );
});

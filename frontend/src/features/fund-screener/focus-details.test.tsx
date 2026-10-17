import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { DailyResearch, Direction } from "../../api/investment";
import * as api from "../../api/study-api";
import DailyResearchReport, {
  DailyResearchStatus,
} from "./components/DailyResearchReport";
import StudyTracking from "./components/StudyTracking";

vi.mock("../../api/study-api", () => ({
  getStudyTracks: vi.fn(),
  getStudyDetail: vi.fn(),
  createStudyTrack: vi.fn(),
  updateStudyVersion: vi.fn(),
  setStudyStatus: vi.fn(),
  checkStudy: vi.fn(),
  addStudyNote: vi.fn(),
  getEligibleStudyTrades: vi.fn(),
  linkStudyTrade: vi.fn(),
}));

const event: NonNullable<DailyResearch["report"]>["events"][number] = {
  id: "energy-policy",
  title: "能源政策线索",
  summary: "已公布安排，实际供应尚未改变。",
  phase: "ANNOUNCED",
  importance: "HIGH",
  sourceIds: ["policy-source"],
  channels: ["供应变化可能传到运输成本"],
  affectedDirectionIds: ["a-share:broad"],
  scenarios: [
    { condition: "供应兑现", impact: "成本可能回落", counter: "运力仍有限" },
  ],
  watchFor: ["下一批实际到货量"],
  unknowns: ["到货规模尚未确认"],
  marketReaction: "UNVERIFIED",
  reasoningType: "MODEL_CONDITIONAL",
};
const daily: DailyResearch = {
  methodVersion: "daily-research-v1",
  state: "FAILED",
  runId: "failed-today",
  reportRunId: "ready-yesterday",
  reportCompletedAt: "2026-10-15T01:00:00Z",
  scheduledFor: "2026-10-16T00:30:00Z",
  startedAt: "2026-10-16T00:35:00Z",
  completedAt: "2026-10-16T00:36:00Z",
  asOf: "2026-10-15T01:00:00Z",
  nextRunAt: "2026-10-17T00:30:00Z",
  errorClass: "ResearchUnavailable",
  report: {
    asOf: "2026-10-15T01:00:00Z",
    summary: "原报告保留有限线索。",
    coverage: [],
    sources: [
      {
        id: "policy-source",
        title: "政策原文",
        url: "https://example.org/policy",
        quote: "原始短引文",
        publishedDate: "2026-10-14",
        publishedAt: null,
        retrievedAt: "2026-10-15T01:00:00Z",
        verification: "SOURCE_CHECKED",
      },
      {
        id: "unrelated",
        title: "未被事件引用的来源",
        url: "https://example.org/unrelated",
        quote: "不应铺开",
        publishedDate: null,
        publishedAt: null,
        retrievedAt: null,
        verification: "REPORTED",
      },
    ],
    events: [event, { ...event }],
    upcoming: Array.from({ length: 5 }, (_, index) => ({
      id: `calendar-${index}`,
      title: `计划日程${index + 1}`,
      phase: "PLANNED" as const,
      scheduledDate: `2026-10-${20 + index}`,
      scheduledAt: null,
      sourceIds: [],
      affectedDirectionIds: [],
      watchFor: ["实际公布值"],
    })),
    energy: [
      {
        id: "brent",
        code: "DCOILBRENTEU",
        label: "布伦特原油",
        value: 70,
        unit: "USD/barrel",
        frequency: "DAILY",
        asOf: "2026-10-15",
        sourceUri: null,
        retrievedAt: null,
        publishedAt: null,
        timeBasis: "OBSERVATION_ONLY",
        state: "READY",
        previousValue: null,
        previousDate: null,
        change: null,
        changePct: null,
        sampleCount: 1,
      },
    ],
  },
};

it("keeps one canonical event with its next check visible and contains only referenced evidence in collapsed details", async () => {
  const track = vi.fn();
  const selectDirection = vi.fn();
  render(
    <DailyResearchReport
      data={daily}
      directions={[
        { id: "a-share:broad", marketLabel: "A股", label: "宽基" } as Direction,
      ]}
      onTrack={track}
      onDirectionSelect={selectDirection}
    />,
  );
  const list = screen.getByRole("list", { name: "重要事件" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(1);
  expect(within(list).getByText(event.summary)).toBeVisible();
  expect(within(list).getByText(/下一批实际到货量/)).toBeVisible();
  expect(within(list).getByText("受影响方向：A股 · 宽基")).toBeVisible();
  expect(
    within(list).getByRole("button", { name: "研究 A股 · 宽基" }),
  ).not.toBeVisible();
  const details = within(list)
    .getByText("供应变化可能传到运输成本")
    .closest("details")!;
  expect(details).not.toHaveAttribute("open");
  expect(within(details).getByText("原始短引文")).toBeInTheDocument();
  expect(screen.queryByText("未被事件引用的来源")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "日报原始来源" }),
  ).not.toBeInTheDocument();
  await userEvent.click(within(details).getByText("查看事件依据与情景"));
  await userEvent.click(
    within(details).getByRole("button", { name: "研究 A股 · 宽基" }),
  );
  expect(selectDirection).toHaveBeenCalledWith("a-share:broad");
  expect(
    within(details).getByRole("link", { name: "政策原文" }),
  ).toHaveAttribute("href", "https://example.org/policy");
  await userEvent.click(
    within(details).getByRole("button", { name: "跟踪能源政策线索" }),
  );
  expect(track).toHaveBeenCalledWith("ready-yesterday", "energy-policy");
  expect(
    screen.queryByRole("region", { name: "能源事实" }),
  ).not.toBeInTheDocument();
});

it("keeps every event and its evidence while deferring the full report summary behind an explicit disclosure", async () => {
  render(
    <DailyResearchReport
      data={{
        ...daily,
        report: {
          ...daily.report!,
          events: Array.from({ length: 5 }, (_, index) => ({
            ...event,
            id: `distinct-${index}`,
            title: `独立事件${index + 1}`,
          })),
        },
      }}
    />,
  );
  expect(screen.getByRole("heading", { name: "重要事件" })).toBeVisible();
  const list = screen.getByRole("list", { name: "重要事件" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(5);
  expect(within(list).getAllByText(event.summary)).toHaveLength(5);
  for (const fact of within(list).getAllByText(event.summary))
    expect(fact).toBeVisible();
  expect(within(list).getAllByText("原始短引文")).toHaveLength(5);
  const summary = screen.getByText(daily.report!.summary);
  expect(summary).not.toBeVisible();
  const details = summary.closest("details")!;
  expect(details).not.toHaveAttribute("open");
  await userEvent.click(within(details).getByText("整体研究摘要"));
  expect(summary).toBeVisible();
});

it("keeps only three planned events in the compact calendar and reveals the remaining plans explicitly", async () => {
  render(<DailyResearchReport data={daily} />);
  const calendar = screen.getByRole("region", { name: "未来关键事件" });
  expect(
    within(within(calendar).getAllByRole("list")[0]).getAllByRole("listitem"),
  ).toHaveLength(3);
  const rest = within(calendar).getByText("计划日程5").closest("details")!;
  expect(rest).not.toHaveAttribute("open");
  expect(within(rest).getByText("计划日程5")).not.toBeVisible();
  await userEvent.click(within(rest).getByText(/查看其余2项日程/));
  expect(within(calendar).getAllByRole("listitem")).toHaveLength(5);
  expect(within(calendar).getAllByText(/计划，尚未发生/)).toHaveLength(5);
});

it("marks unresolved affected directions honestly and never tracks an event using the failed task identity", () => {
  render(
    <DailyResearchReport
      data={{
        ...daily,
        reportRunId: null,
        report: {
          ...daily.report!,
          events: [{ ...event, affectedDirectionIds: ["unregistered"] }],
        },
      }}
      onTrack={vi.fn()}
    />,
  );
  expect(screen.getByText("受影响方向：待核对")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "跟踪能源政策线索" }),
  ).not.toBeInTheDocument();
});

it("separates the failed task from the actual historical report inside execution details and names an absent valid report", () => {
  const view = render(<DailyResearchReport data={daily} />);
  const runtime = screen.getByText("ResearchUnavailable").closest("details")!;
  expect(runtime).not.toHaveAttribute("open");
  expect(within(runtime).getByText(/failed-today/)).toBeInTheDocument();
  expect(within(runtime).getByText(/ready-yesterday/)).toBeInTheDocument();
  expect(
    within(runtime).getByText(/实际开始：2026年10月16日 08:35/),
  ).toBeInTheDocument();
  expect(
    within(runtime).getByText(/结束：2026年10月16日 08:36/),
  ).toBeInTheDocument();
  view.rerender(
    <DailyResearchReport
      data={{ ...daily, reportRunId: null, report: null }}
    />,
  );
  expect(screen.getByText("暂无有效日报")).toBeVisible();
});

it("exposes today's due-but-undispatched state without inferring that the service has stopped", () => {
  render(
    <DailyResearchReport
      data={{
        ...daily,
        state: "NOT_RUN",
        scheduleState: "DUE_NOT_DISPATCHED",
        runId: null,
        scheduledFor: null,
        startedAt: null,
        completedAt: null,
      }}
    />,
  );
  const runtime = screen.getByText("执行与报告状态").closest("details")!;
  expect(
    within(runtime).getByText(/今日到期，尚未登记派发/),
  ).toBeInTheDocument();
  expect(
    screen.queryByText(/服务已停止|服务离线|服务正在运行/),
  ).not.toBeInTheDocument();
});

it("does not describe yesterday's failed task as a historical report when no READY report exists", () => {
  render(
    <DailyResearchReport
      data={{ ...daily, state: "STALE", reportRunId: null, report: null }}
    />,
  );
  expect(screen.getByRole("heading", { name: "暂无有效日报" })).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "历史日报" }),
  ).not.toBeInTheDocument();
});

it("lets the shared status distinguish an absent READY report while retaining historical-report compatibility", () => {
  const view = render(<DailyResearchStatus state="STALE" hasReport={false} />);
  expect(screen.getByText("暂无有效日报")).toBeInTheDocument();
  view.rerender(<DailyResearchStatus state="STALE" hasReport />);
  expect(screen.getByText("历史日报")).toBeInTheDocument();
  view.rerender(<DailyResearchStatus state="STALE" />);
  expect(screen.getByText("历史日报")).toBeInTheDocument();
});

const snapshot: api.StudySnapshot = {
  title: "已有主题",
  thesis: "冻结假设",
  transmission: [],
  supports: [],
  counterFactors: [],
  watchFor: ["等待下一次官方发布"],
  invalidationConditions: [],
  sources: [],
  origin: { originType: "SELECTION" },
  scope: { indexId: "hs300" },
  createdAt: "2026-10-16T01:00:00Z",
  expiresOn: "2026-11-15",
  conditions: [],
  observations: [],
};
const study: api.StudyDetail = {
  track: {
    id: "mine",
    title: "已有主题",
    enabled: true,
    generation: 1,
    status: "OBSERVING",
    createdAt: snapshot.createdAt,
    updatedAt: snapshot.createdAt,
    unreadCount: 1,
    scope: snapshot.scope,
  },
  version: {
    id: "version-1",
    version: 1,
    generation: 1,
    snapshot,
    createdAt: snapshot.createdAt,
  },
  versions: [],
  checks: [],
  notes: [],
  tradeLinks: [],
  history: { limit: 50, offset: 0, hasMore: false },
  capabilities: { canCheck: true, canEdit: true, canLinkTrade: true },
};
function showStudy() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <StudyTracking actor="mine" scope={{ indexId: "hs300" }} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [study.track],
    total: 1,
  });
  vi.mocked(api.getStudyDetail).mockResolvedValue(study);
  vi.mocked(api.createStudyTrack).mockResolvedValue(study);
});

it("starts with the private study list and unknown next condition, then opens a declared condition only for the selected topic", async () => {
  showStudy();
  const list = await screen.findByRole("navigation", { name: "我的研究主题" });
  expect(
    await within(list).findByRole("button", {
      name: /已有主题.*观察中.*1项未读变化/,
    }),
  ).toBeInTheDocument();
  expect(
    within(list).getByText(/下一条件：选择主题后读取冻结条件/),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("spinbutton", { name: "观察天数" }),
  ).not.toBeVisible();
  expect(
    screen.queryByRole("region", { name: "决策与人工复盘" }),
  ).not.toBeInTheDocument();
  expect(api.getStudyDetail).not.toHaveBeenCalled();
  await userEvent.click(within(list).getByRole("button", { name: /已有主题/ }));
  expect(await screen.findByText("冻结假设")).toBeVisible();
  expect(screen.getByText(/下一条件：等待下一次官方发布/)).toBeVisible();
});

it("opens new research only explicitly and preserves the thirty-day create contract", async () => {
  showStudy();
  await screen.findByRole("button", { name: /已有主题/ });
  const user = userEvent.setup();
  expect(
    screen.getByRole("button", { name: "冻结研究并跟踪" }),
  ).not.toBeVisible();
  await user.click(screen.getByText("新建研究"));
  expect(screen.getByRole("spinbutton", { name: "观察天数" })).toHaveValue(30);
  await user.click(screen.getByRole("button", { name: "冻结研究并跟踪" }));
  expect(api.createStudyTrack).toHaveBeenCalledWith({
    originType: "SELECTION",
    scope: { indexId: "hs300" },
    horizonDays: 30,
  });
  expect(await screen.findByText("冻结假设")).toBeVisible();
});

it("explains an empty private list without inventing a next condition", async () => {
  vi.mocked(api.getStudyTracks).mockResolvedValue({ items: [], total: 0 });
  showStudy();
  expect(await screen.findByText(/暂无我的研究主题/)).toBeVisible();
  expect(screen.queryByText(/下一条件：等待/)).not.toBeInTheDocument();
  expect(api.getStudyDetail).not.toHaveBeenCalled();
});

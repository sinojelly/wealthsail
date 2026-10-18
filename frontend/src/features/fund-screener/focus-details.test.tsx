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
import { readingText } from "./research-reading";

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
it("keeps the existing impact conditional and readable before opening the full report", () => {
  render(<DailyResearchReport data={daily} />);
  const list = screen.getByRole("list", { name: "重要事件" });
  expect(
    within(list).getByText(/潜在影响：.*条件：供应兑现.*影响：成本可能回落/),
  ).toBeVisible();
  expect(event.scenarios[0]).toEqual({
    condition: "供应兑现",
    impact: "成本可能回落",
    counter: "运力仍有限",
  });
});

it("shows the canonical event as compact facts and impact and opens its complete evidence explicitly", async () => {
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
  expect(within(list).getByText(`事实：${event.summary}`)).toBeVisible();
  expect(
    within(list).getByText(/潜在影响：.*供应变化可能传到运输成本/),
  ).toBeVisible();
  expect(within(list).getByText("受影响方向：A股 · 宽基")).toBeVisible();
  const details = screen.getByText("阅读完整日报").closest("details")!;
  expect(details).not.toHaveAttribute("open");
  expect(within(details).getByText("原始短引文")).toBeInTheDocument();
  expect(screen.queryByText("未被事件引用的来源")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "日报原始来源" }),
  ).not.toBeInTheDocument();
  await userEvent.click(
    within(list).getByRole("button", {
      name: "查看能源政策线索事件依据与情景",
    }),
  );
  expect(details).toHaveAttribute("open");
  expect(within(details).getByText(event.summary)).toBeVisible();
  await userEvent.click(
    within(details).getByRole("button", { name: "研究 A股 · 宽基" }),
  );
  expect(selectDirection).toHaveBeenCalledWith("a-share:broad");
  expect(
    within(details).getByRole("link", { name: "政策原文" }),
  ).toHaveAttribute("href", "https://example.org/policy");
  expect(track).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("region", { name: "能源事实" }),
  ).not.toBeInTheDocument();
});

it("shows at most three events and retains every original event and quotation in the collapsed full report", async () => {
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
  expect(screen.getByRole("heading", { name: "关键经济要闻" })).toBeVisible();
  const list = screen.getByRole("list", { name: "重要事件" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(3);
  for (const fact of within(list).getAllByText(`事实：${event.summary}`))
    expect(fact).toBeVisible();
  const summary = screen.getByText(daily.report!.summary);
  expect(summary).not.toBeVisible();
  const details = summary.closest("details")!;
  expect(details).not.toHaveAttribute("open");
  expect(within(details).getAllByText(event.summary)).toHaveLength(5);
  expect(within(details).getAllByText("原始短引文")).toHaveLength(5);
  await userEvent.click(within(details).getByText("阅读完整日报"));
  expect(summary).toBeVisible();
  expect(
    within(details).getByRole("heading", { name: "独立事件5" }),
  ).toBeVisible();
});

it("defers every planned event until the user expands the secondary calendar", async () => {
  render(<DailyResearchReport data={daily} />);
  const calendar = screen.getByRole("region", { name: "未来关键事件" });
  expect(within(calendar).getByText("计划日程1")).not.toBeVisible();
  expect(within(calendar).getByText("计划日程5")).not.toBeVisible();
  const disclosure = within(calendar)
    .getByText(/接下来关注什么/)
    .closest("details")!;
  expect(disclosure).not.toHaveAttribute("open");
  await userEvent.click(within(calendar).getByText(/接下来关注什么/));
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
  expect(
    within(screen.getByRole("list", { name: "重要事件" })).getByText(
      "受影响方向：待核对",
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "持续跟踪能源政策线索" }),
  ).not.toBeInTheDocument();
});

it("ranks importance before checked-reference counts, deduplicates identities and keeps tied input order without changing the report", () => {
  const rows = [
    { ...event, id: "medium", title: "相关中等事件", importance: "MEDIUM" },
    {
      ...event,
      id: "high-unchecked",
      title: "待核对高影响事件",
      sourceIds: ["unrelated"],
    },
    { ...event, id: "high-first", title: "先到高影响事件" },
    { ...event, id: "high-first", title: "重复事件" },
    { ...event, id: "high-second", title: "后到高影响事件" },
  ];
  const original = JSON.stringify(rows);
  Object.freeze(rows);
  render(
    <DailyResearchReport
      data={{ ...daily, report: { ...daily.report!, events: rows } }}
    />,
  );
  const list = screen.getByRole("list", { name: "重要事件" });
  expect(
    within(list)
      .getAllByRole("heading")
      .map((heading) => heading.textContent),
  ).toEqual(["先到高影响事件", "后到高影响事件", "待核对高影响事件"]);
  expect(screen.queryByText("重复事件")).not.toBeInTheDocument();
  expect(JSON.stringify(rows)).toBe(original);
});

it("bounds and translates the reading preview while preserving complete original facts and numbers in the full report", async () => {
  const original = `2026年10月15日同比5.0%，historyQuality=CONFLICT；PIT尚缺；SOURCE_CHECKED、IMPLEMENTED。${"后续影响需要核对。".repeat(35)}`;
  render(
    <DailyResearchReport
      data={{
        ...daily,
        report: { ...daily.report!, events: [{ ...event, summary: original }] },
      }}
    />,
  );
  const list = screen.getByRole("list", { name: "重要事件" });
  const fact = within(list).getByText(/^事实：/);
  expect(fact.textContent!.length).toBeLessThanOrEqual(145);
  expect(fact).toHaveTextContent("2026年10月15日同比5.0%");
  expect(fact).toHaveTextContent("历史数据存在冲突");
  expect(list).not.toHaveTextContent(
    /historyQuality|CONFLICT|PIT|SOURCE_CHECKED|IMPLEMENTED/,
  );
  const full = screen.getByText("阅读完整日报").closest("details")!;
  expect(within(full).getByText(readingText(original, 5000))).not.toBeVisible();
  await userEvent.click(within(full).getByText("阅读完整日报"));
  expect(within(full).getByText(readingText(original, 5000))).toBeVisible();
  expect(original).toContain("historyQuality=CONFLICT");
});

it("keeps current asset content after the important events and before the collapsed calendar and report", () => {
  render(
    <DailyResearchReport data={daily}>
      <section aria-label="当前资产观察">资产观察</section>
    </DailyResearchReport>,
  );
  const events = screen.getByRole("list", { name: "重要事件" });
  const assets = screen.getByRole("region", { name: "当前资产观察" });
  const calendar = screen.getByRole("region", { name: "未来关键事件" });
  const full = screen.getByText("阅读完整日报").closest("details")!;
  expect(
    events.compareDocumentPosition(assets) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    assets.compareDocumentPosition(calendar) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    calendar.compareDocumentPosition(full) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

it("keeps news as read-only evidence before and after opening the full report", async () => {
  const track = vi.fn();
  render(<DailyResearchReport data={daily} onTrack={track} />);
  expect(
    screen.queryByRole("button", { name: /持续跟踪/ }),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByText("阅读完整日报"));
  expect(
    screen.queryByRole("button", { name: /持续跟踪/ }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "确认研究假设" }),
  ).not.toBeInTheDocument();
  expect(track).not.toHaveBeenCalled();
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

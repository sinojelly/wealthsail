import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import type { OpportunityContext } from "../../api/investment";
import { buildEconomicStories } from "./economic-story-model";
import {
  EconomicEvents,
  EconomicEventTeasers,
} from "./components/EconomicEvents";

function context(count = 1): OpportunityContext {
  return {
    state: "READY",
    items: [],
    dailyResearch: {
      methodVersion: "daily-v1",
      state: "FAILED",
      runId: "current-run",
      reportRunId: "historical-run",
      scheduledFor: null,
      startedAt: null,
      completedAt: null,
      asOf: null,
      nextRunAt: null,
      report: {
        asOf: "2026-11-05T08:00:00+08:00",
        summary: "历史报告",
        coverage: [],
        sources: [],
        events: [
          {
            id: "history",
            title: "历史独立事项",
            summary: "历史事实保留",
            phase: "REPORTED",
            importance: "LOW",
            sourceIds: [],
            channels: [],
            affectedDirectionIds: [],
            scenarios: [],
            watchFor: [],
            unknowns: [],
            marketReaction: "UNVERIFIED",
            reasoningType: "MODEL_CONDITIONAL",
          },
        ],
        upcoming: [],
        energy: [],
      },
      economicResearch: {
        version: "economic-research-delivery-v1",
        researchId: "current-run",
        status: "PARTIAL",
        asOf: "2026-11-07T08:00:00+08:00",
        validatedAt: null,
        recordedAt: null,
        archiveState: "ARCHIVED",
        archivePending: false,
        acceptedContentComplete: true,
        actionable: false,
        observations: {
          sources: [
            {
              id: "checked",
              title: "核对原文",
              url: "https://example.test/news",
              publishedAt: "2026-11-06T09:00:00+08:00",
              verification: "SOURCE_CHECKED",
              quote: "来源引文",
            },
            {
              id: "unsafe",
              title: "危险来源",
              url: "javascript:alert(1)",
              verification: "UNVERIFIED",
            },
          ],
          industryCycleReviews: [],
          valueDirections: [],
          upcoming: [],
          events: Array.from({ length: count }, (_, index) => ({
            id: `event-${index}`,
            title: `本次事项${index + 1}`,
            summary: `独立事实${index + 1}`,
            phase: "ANNOUNCED",
            sourceIds: ["checked", "unsafe", "missing"],
            channels: index === 0 ? ["能源与航运"] : ["利率"],
            scenarios: [
              {
                condition: "若供给持续下降",
                impact: "成本可能上升",
                counter: "需求下降可能抵消",
              },
            ],
            watchFor: ["后续实际供给"],
            unknowns: ["持续时间未知"],
          })),
        },
      },
    },
  };
}

it("separates fact dates, source checking and conditional details without certifying a market move", async () => {
  render(<EconomicEvents data={context()} />);
  const card = screen.getByRole("article", { name: "本次事项1" });
  expect(within(card).getByText("独立事实1")).toBeVisible();
  expect(within(card).queryByText(/事件发生时间未单独提供/)).not.toBeInTheDocument();
  expect(within(card).getByText(/来源发布：2026年11月6日 09:00/)).toBeVisible();
  expect(within(card).getByText(/非事件发生时间/)).toBeVisible();
  expect(within(card).getByText("2026年11月7日 08:00")).toHaveAttribute(
    "dateTime",
    "2026-11-07T08:00:00+08:00",
  );
  expect(
    within(card).getByText(
      /引用来源 3 项.*可读 2 项.*原文核对 1 项.*缺失 1 项/,
    ),
  ).toBeVisible();
  expect(within(card).getByText("尚无可靠市场反应数据")).toBeVisible();
  await userEvent.click(within(card).getByText("阅读传导、情景与来源"));
  expect(within(card).getByText("若供给持续下降")).toBeVisible();
  expect(within(card).getByText("需求下降可能抵消")).toBeVisible();
  expect(within(card).getByText("持续时间未知")).toBeVisible();
  expect(within(card).getByRole("link", { name: "核对原文" })).toHaveAttribute(
    "href",
    "https://example.test/news",
  );
  expect(
    within(card).queryByRole("link", { name: "危险来源" }),
  ).not.toBeInTheDocument();
  expect(within(card).getByText(/来源发布时间：2026年11月6日/)).toBeVisible();
  expect(within(card).getByText(/经济小课堂 · 通用机制/)).toBeVisible();
  expect(
    within(card).getByText(/原文引用核对不等于事实或因果认证/),
  ).toBeVisible();
});

it("shows at most eight cards before more and makes classification and loaded research-date browsing work", async () => {
  render(<EconomicEvents data={context(10)} />);
  expect(screen.getAllByRole("article")).toHaveLength(8);
  await userEvent.click(screen.getByRole("button", { name: /查看更多事件/ }));
  expect(screen.getAllByRole("article")).toHaveLength(11);
  await userEvent.click(screen.getByRole("button", { name: "国际局势" }));
  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(screen.getByRole("article", { name: "本次事项1" })).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "全部" }));
  await userEvent.selectOptions(
    screen.getByLabelText("研究日期（仅已加载归档）"),
    "2026-11-05",
  );
  expect(screen.getAllByRole("article")).toHaveLength(1);
  expect(screen.getByRole("article", { name: "历史独立事项" })).toBeVisible();
  expect(screen.getByText(/仅浏览本页已加载的研究日期/)).toBeVisible();
  await userEvent.click(
    within(screen.getByRole("article", { name: "历史独立事项" })).getByText(
      "阅读传导、情景与来源",
    ),
  );
  expect(
    screen.getByRole("link", { name: "查看本份报告出处" }),
  ).toHaveAttribute("href", expect.stringContaining("researchTab=macro"));
  const reportSource = new URL(
    screen
      .getByRole("link", { name: "查看本份报告出处" })
      .getAttribute("href")!,
    window.location.href,
  );
  expect(reportSource.searchParams.get("reportRunId")).toBe("historical-run");
  expect(reportSource.hash).toBe("#latest-successful-daily-report");
  expect(screen.getByText(/报告出处不表示已核实的事件进展/)).toBeVisible();
});

it("shares teaser IDs with selected detail and never pads three summaries", async () => {
  const data = context(10);
  const onOpen = vi.fn();
  const view = render(
    <EconomicEventTeasers data={data} onOpenEvent={onOpen} />,
  );
  expect(screen.getAllByRole("button", { name: /阅读事件/ })).toHaveLength(3);
  await userEvent.click(
    screen.getByRole("button", { name: "阅读事件：本次事项1" }),
  );
  expect(onOpen).toHaveBeenCalledWith(
    expect.stringMatching(/^CURRENT_ACCEPTED:current-run:event-0:/),
  );
  const openedId = onOpen.mock.calls[0][0];
  expect(openedId).toBe(buildEconomicStories(data)[0].id);
  const ninthId = buildEconomicStories(data)[8].id;
  view.rerender(<EconomicEvents data={data} selectedEventId={ninthId} />);
  expect(screen.getAllByRole("article")).toHaveLength(8);
  const selected = screen.getByRole("article", { name: "本次事项9" });
  expect(within(selected).getByText("若供给持续下降")).toBeVisible();
  expect(selected.querySelector("details")).toHaveAttribute("open");
  view.rerender(
    <EconomicEventTeasers
      data={{ state: "READY", items: [] }}
      onOpenEvent={onOpen}
    />,
  );
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("retains an independent official publication when current research is pending or running", () => {
  const data = context();
  data.dailyResearch!.state = "RUNNING";
  data.dailyResearch!.economicResearch!.status = "PENDING";
  data.events = [
    {
      id: "official",
      title: "独立官方发布",
      category: "DATA_RELEASE",
      sourceType: "OFFICIAL_FACT",
      publisher: "统计机构",
      sourceUri: "https://example.test/official",
      publishedAt: "2026-11-04T08:00:00+08:00",
      retrievedAt: null,
      summary: "独立公开事实",
      directionIds: [],
    },
  ];
  render(<EconomicEvents data={data} />);
  expect(screen.getByText(/本次研究正在执行/)).toBeVisible();
  expect(screen.getByText(/本次经济观察待归档/)).toBeVisible();
  expect(screen.queryByText("本次事项1")).not.toBeInTheDocument();
  expect(screen.getByRole("article", { name: "独立官方发布" })).toBeVisible();
  expect(screen.getByText("独立官方资料")).toBeVisible();
});

it("explains empty, partial and missing selected records without claiming complete news coverage", () => {
  const view = render(
    <EconomicEvents
      data={{ state: "READY", items: [] }}
      selectedEventId="not-loaded"
    />,
  );
  expect(screen.getByText(/所选事件不在已加载资料中/)).toBeVisible();
  expect(screen.getByText(/暂无已加载的可读经济事件/)).toBeVisible();
  const data = context();
  data.dailyResearch!.economicResearch!.acceptedContentComplete = false;
  view.rerender(<EconomicEvents data={data} />);
  expect(screen.getByText(/本次观察仅加载部分安全内容/)).toBeVisible();
});

it("keeps program energy observations separate with their own dates and explicit asset-data gaps", () => {
  const data = context();
  data.dailyResearch!.report!.energy = [
    {
      id: "brent",
      code: "BRENT",
      label: "布伦特",
      value: 74.2,
      unit: "USD/barrel",
      frequency: "DAILY",
      asOf: "2026-11-04",
      sourceUri: "https://example.test/energy",
      retrievedAt: "2026-11-05T01:00:00Z",
      publishedAt: null,
      timeBasis: "OBSERVATION_ONLY",
      state: "STALE",
      previousValue: 73.2,
      previousDate: "2026-11-03",
      change: 1,
      changePct: 1.366,
      sampleCount: 2,
    },
  ];
  render(<EconomicEvents data={data} />);
  const energy = screen.getByRole("region", { name: "独立能源观察" });
  expect(within(energy).getByText(/74.2 美元\/桶/)).toBeVisible();
  expect(within(energy).getByText(/观察日期：2026年11月4日/)).toBeVisible();
  expect(within(energy).getByText(/前值日期：2026年11月3日/)).toBeVisible();
  expect(within(energy).getByText(/不证明由上述事件导致/)).toBeVisible();
  expect(
    screen.getByText(
      /股票、汇率、黄金等资产尚缺与本事件对应的可靠市场反应证据/,
    ),
  ).toBeVisible();
});

it("keeps opened selection and content after source-only refresh and honours report-completion date fallback", async () => {
  const data = context();
  const selectedId = buildEconomicStories(data)[0].id;
  const view = render(
    <EconomicEvents data={data} selectedEventId={selectedId} />,
  );
  const updated = structuredClone(data);
  updated.dailyResearch!.economicResearch!.observations.sources[0].verification =
    "UNVERIFIED";
  updated.dailyResearch!.economicResearch!.observations.sources[0].retrievedAt =
    "2026-11-07T11:00:00+08:00";
  delete updated.dailyResearch!.report!.asOf;
  updated.dailyResearch!.reportCompletedAt = "2026-11-05T08:00:00+08:00";
  view.rerender(<EconomicEvents data={updated} selectedEventId={selectedId} />);
  expect(
    screen.queryByText(/所选事件不在已加载资料中/),
  ).not.toBeInTheDocument();
  expect(
    within(screen.getByRole("article", { name: "本次事项1" })).getByText(
      "持续时间未知",
    ),
  ).toBeVisible();
  await userEvent.selectOptions(
    screen.getByLabelText("研究日期（仅已加载归档）"),
    "2026-11-05",
  );
  expect(screen.getByText(/报告完成日期：2026年11月5日/)).toBeVisible();
  expect(
    screen.queryByText("2026年11月5日 08:00", { selector: "time" }),
  ).not.toBeInTheDocument();
});

it.each(["2026-11-02", null])(
  "keeps archived plan occurrence unconfirmed for scheduled date %s",
  (scheduledDate) => {
    const data = context();
    data.dailyResearch!.report!.upcoming.push({
      id: "past-plan",
      title: "归档计划事项",
      phase: "PLANNED",
      scheduledDate,
      scheduledAt: null,
      sourceIds: [],
      affectedDirectionIds: [],
      watchFor: [],
    });
    render(<EconomicEvents data={data} />);
    const plan = screen.getByRole("article", { name: "归档计划事项" });
    expect(
      within(plan).getByText(/原研究计划时间：.*是否发生未确认/),
    ).toBeVisible();
    expect(within(plan).queryByText(/尚未发生/)).not.toBeInTheDocument();
    expect(within(plan).getByText("事件发生时间未单独提供")).toBeVisible();
    if (scheduledDate)
      expect(within(plan).getByText(/2026年11月2日/)).toBeVisible();
    else expect(within(plan).getByText(/原研究计划时间：未确认/)).toBeVisible();
  },
);

it("shows unverified event facts before details even when original phase is implemented and quotation checked", async () => {
  const data = context();
  const report = data.dailyResearch!.report!;
  report.events[0].phase = "IMPLEMENTED";
  report.events[0].factVerification = "UNVERIFIED";
  report.sources = [
    {
      id: "report-source",
      title: "已核对报告引文",
      url: "https://example.test/checked-report",
      quote: "原文引文",
      publishedDate: "2026-11-04",
      publishedAt: null,
      retrievedAt: null,
      verification: "SOURCE_CHECKED",
    },
  ];
  report.events[0].sourceIds = ["report-source"];
  data.events = [
    {
      id: "official",
      title: "独立采集事项",
      summary: "官方公开摘要",
      category: "DATA_RELEASE",
      sourceType: "OFFICIAL_FACT",
      publisher: "官方机构",
      sourceUri: "https://example.test/official",
      publishedAt: null,
      retrievedAt: null,
      directionIds: [],
    },
  ];
  render(<EconomicEvents data={data} />);
  const event = screen.getByRole("article", { name: "历史独立事项" });
  expect(within(event).getByText("已实施")).toBeVisible();
  expect(within(event).getByText(/原文核对 1 项/)).toBeVisible();
  expect(within(event).getByText("事件事实未独立核验")).toBeVisible();
  expect(
    within(screen.getByRole("article", { name: "独立采集事项" })).getByText(
      "事件事实未独立核验",
    ),
  ).toBeVisible();
  expect(
    within(event).getByText("阅读传导、情景与来源").closest("details"),
  ).not.toHaveAttribute("open");
  await userEvent.click(within(event).getByText("阅读传导、情景与来源"));
  expect(
    within(event).getByText(/原事件事实核验标记：UNVERIFIED/),
  ).toBeVisible();
});

it("prioritizes later high-importance teasers with stable ties while retaining archive input order", () => {
  const data = context(5);
  const events = data.dailyResearch!.economicResearch!.observations.events;
  events.forEach((event, index) => {
    event.importance = index < 3 ? "LOW" : "HIGH";
  });
  const original = structuredClone(data);
  const view = render(
    <EconomicEventTeasers data={data} onOpenEvent={vi.fn()} />,
  );
  expect(
    screen
      .getAllByRole("heading", { level: 4 })
      .map((item) => item.textContent),
  ).toEqual(["本次事项4", "本次事项5", "本次事项1"]);
  view.rerender(<EconomicEvents data={data} />);
  expect(
    screen
      .getAllByRole("article")
      .map((item) => item.getAttribute("aria-label")),
  ).toEqual([
    "本次事项1",
    "本次事项2",
    "本次事项3",
    "本次事项4",
    "本次事项5",
    "历史独立事项",
  ]);
  expect(data).toEqual(original);
});

it("uses known research recency after importance and keeps new independent sources separate from retained facts", async () => {
  const data = context();
  data.dailyResearch!.economicResearch!.observations.events[0].importance =
    "HIGH";
  data.dailyResearch!.report!.events[0].importance = "HIGH";
  const before = buildEconomicStories(data);
  data.events = [
    {
      id: "history",
      title: "历史独立事项",
      summary: "刚采集的独立官方资料，不替换旧事实",
      category: "DATA_RELEASE",
      sourceType: "OFFICIAL_FACT",
      publisher: "独立发布机构",
      sourceUri: "https://example.test/new-official",
      publishedAt: "2026-11-07T10:00:00+08:00",
      retrievedAt: "2026-11-07T10:01:00+08:00",
      directionIds: [],
    },
  ];
  const onOpen = vi.fn();
  const view = render(
    <EconomicEventTeasers data={data} onOpenEvent={onOpen} />,
  );
  const items = screen.getAllByRole("listitem");
  expect(
    items.map(
      (item) => within(item).getByRole("heading", { level: 4 }).textContent,
    ),
  ).toEqual(["本次事项1", "历史独立事项", "历史独立事项"]);
  expect(within(items[0]).getByText(/2026-11-07/)).toBeVisible();
  expect(within(items[1]).getByText(/2026-11-05/)).toBeVisible();
  expect(within(items[2]).getByText(/独立公开记录/)).toBeVisible();
  await userEvent.click(within(items[1]).getByRole("button"));
  expect(onOpen).toHaveBeenCalledWith(before[1].id);
  view.rerender(<EconomicEvents data={data} selectedEventId={before[1].id} />);
  const archive = screen.getAllByRole("article");
  expect(archive[0]).toHaveAttribute("data-event-id", before[0].id);
  expect(archive[1]).toHaveAttribute("data-event-id", before[1].id);
  expect(within(archive[1]).getByText("历史事实保留")).toBeVisible();
  expect(
    within(archive[2]).getByText("刚采集的独立官方资料，不替换旧事实"),
  ).toBeVisible();
  expect(
    within(archive[2]).getByText("独立发布，不隶属研究日期"),
  ).toBeVisible();
});

it("breaks equal-importance teaser ties by known research date rather than insertion origin", () => {
  const data = context();
  data.dailyResearch!.economicResearch!.observations.events[0].importance =
    "HIGH";
  data.dailyResearch!.economicResearch!.asOf = "2026-11-04T08:00:00+08:00";
  data.dailyResearch!.report!.events[0].importance = "HIGH";
  render(<EconomicEventTeasers data={data} onOpenEvent={vi.fn()} />);
  expect(
    screen
      .getAllByRole("heading", { level: 4 })
      .map((item) => item.textContent),
  ).toEqual(["历史独立事项", "本次事项1"]);
});

it("labels impossible evidence and archive dates as unverifiable while preserving raw records and missing dates", () => {
  const data = context();
  data.dailyResearch!.economicResearch!.asOf = "2026-02-30T08:00:00+08:00";
  delete data.dailyResearch!.report!.asOf;
  data.dailyResearch!.reportCompletedAt = "2026-02-30T23:00:00Z";
  data.dailyResearch!.reportSlotDate = "2026-02-30";
  render(<EconomicEvents data={data} />);
  const options = within(
    screen.getByLabelText("研究日期（仅已加载归档）"),
  ).getAllByRole("option");
  expect(options).toHaveLength(1);
  const current = screen.getByRole("article", { name: "本次事项1" });
  expect(
    within(current).getByText(/日期不可核实.*2026-02-30T08:00:00\+08:00/),
  ).toBeVisible();
  expect(within(current).queryByText(/2026年3月/)).not.toBeInTheDocument();
  const report = screen.getByRole("article", { name: "历史独立事项" });
  expect(within(report).getByText("研究证据日期未提供")).toBeVisible();
  expect(
    within(report).getByText(
      /报告完成日期：.*日期不可核实.*2026-02-30T23:00:00Z/,
    ),
  ).toBeVisible();
  expect(
    within(report).getByText(/报告所属日期：.*日期不可核实.*2026-02-30/),
  ).toBeVisible();
});

it("labels an invalid original research date separately from a valid completion-date fallback", () => {
  const data = context();
  data.dailyResearch!.report!.asOf = "2026-02-30";
  data.dailyResearch!.reportCompletedAt = "2026-02-28T23:00:00Z";
  render(<EconomicEvents data={data} />);
  const report = screen.getByRole("article", { name: "历史独立事项" });
  expect(
    within(report).getByText(/研究日期：.*日期不可核实.*2026-02-30/),
  ).toBeVisible();
  expect(within(report).getByText(/报告完成日期：2026年3月1日/)).toBeVisible();
  expect(
    within(screen.getByLabelText("研究日期（仅已加载归档）")).getByRole(
      "option",
      { name: "2026-03-01" },
    ),
  ).toBeInTheDocument();
});

it.each(["", "invalid-date"])(
  "distinguishes a supplied invalid evidence date %j from a missing date in cards and teasers",
  (asOf) => {
    const data = context();
    data.dailyResearch!.economicResearch!.asOf = asOf;
    const view = render(<EconomicEvents data={data} />);
    const card = screen.getByRole("article", { name: "本次事项1" });
    expect(within(card).getByText(/日期不可核实/)).toBeVisible();
    expect(
      within(card).queryByText("研究证据日期未提供"),
    ).not.toBeInTheDocument();
    view.rerender(<EconomicEventTeasers data={data} onOpenEvent={vi.fn()} />);
    expect(screen.getByText(/本次安全经济观察.*日期不可核实/)).toBeVisible();
  },
);

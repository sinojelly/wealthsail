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

it("shows one publication date and a compact title, keeping audit and summary behind expansion", async () => {
  render(<EconomicEvents data={context()} />);
  const card = screen.getByRole("article", { name: "本次事项1" });
  const summary = card.querySelector("summary")!;
  expect(within(summary).getByText("11-06 09:00")).toHaveAttribute(
    "dateTime",
    "2026-11-06T09:00:00+08:00",
  );
  expect(summary.querySelectorAll("time")).toHaveLength(1);
  expect(within(summary).getByText("本次事项1")).toBeVisible();
  expect(
    within(summary).queryByText(/研究日期|引用来源|原文核对 1 项|发生了什么/),
  ).not.toBeInTheDocument();
  expect(within(card).getByText("独立事实1")).not.toBeVisible();
  expect(within(card).getByText(/原文核对 1 项/)).not.toBeVisible();
  await userEvent.click(summary);
  expect(within(card).getByText("独立事实1")).toBeVisible();
  expect(within(card).getByText(/非事件发生时间/)).toBeVisible();
  expect(within(card).getByText("2026年11月7日 08:00")).toHaveAttribute(
    "dateTime",
    "2026-11-07T08:00:00+08:00",
  );
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
  expect(
    within(card).getByText(/原文引用核对不等于事实或因果认证/),
  ).toBeVisible();
  await userEvent.click(summary);
  expect(within(card).getByText("独立事实1")).not.toBeVisible();
});

it("keeps loaded archive category/date filters and pagination without dropping the report source deep link", async () => {
  render(<EconomicEvents data={context(10)} />);
  expect(screen.getAllByRole("article")).toHaveLength(8);
  await userEvent.click(screen.getByRole("button", { name: /查看更多事件/ }));
  expect(screen.getAllByRole("article")).toHaveLength(11);
  await userEvent.click(screen.getByRole("button", { name: "国际局势" }));
  expect(screen.getAllByRole("article")).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "全部" }));
  await userEvent.selectOptions(
    screen.getByLabelText("研究日期（仅已加载归档）"),
    "2026-11-05",
  );
  expect(screen.getAllByRole("article")).toHaveLength(1);
  const historical = screen.getByRole("article", { name: "历史独立事项" });
  await userEvent.click(historical.querySelector("summary")!);
  const source = new URL(
    screen
      .getByRole("link", { name: "查看本份报告出处" })
      .getAttribute("href")!,
    window.location.href,
  );
  expect(source.searchParams.get("reportRunId")).toBe("historical-run");
  expect(source.hash).toBe("#latest-successful-daily-report");
  expect(screen.getByText(/仅浏览本页已加载的研究日期/)).not.toBeVisible();
  await userEvent.click(screen.getByText("归档与阅读说明"));
  expect(screen.getByText(/仅浏览本页已加载的研究日期/)).toBeVisible();
});

it("shares teaser IDs with selected details even beyond the first eight rows", async () => {
  const data = context(10);
  const onOpen = vi.fn();
  const view = render(
    <EconomicEventTeasers data={data} onOpenEvent={onOpen} />,
  );
  expect(screen.getAllByRole("button", { name: /阅读事件/ })).toHaveLength(3);
  expect(
    screen.queryByText(/事件事实未独立核验|发生了什么/),
  ).not.toBeInTheDocument();
  await userEvent.click(
    screen.getByRole("button", { name: "阅读事件：本次事项1" }),
  );
  expect(onOpen).toHaveBeenCalledWith(buildEconomicStories(data)[0].id);
  const ninth = buildEconomicStories(data)[8].id;
  view.rerender(<EconomicEvents data={data} selectedEventId={ninth} />);
  expect(screen.getAllByRole("article")).toHaveLength(8);
  const selected = screen.getByRole("article", { name: "本次事项9" });
  expect(selected.querySelector("details")).toHaveAttribute("open");
  expect(within(selected).getByText("持续时间未知")).toBeVisible();
});

it("retains official news while pending or running research diagnostics stay collapsed", async () => {
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
  expect(screen.queryByText("本次事项1")).not.toBeInTheDocument();
  expect(screen.getByRole("article", { name: "独立官方发布" })).toBeVisible();
  expect(screen.getByText(/本次研究正在执行/)).not.toBeVisible();
  await userEvent.click(screen.getByText(/条已加载新闻 · 归档状态/));
  expect(screen.getByText(/本次研究正在执行/)).toBeVisible();
});

it("explains empty and missing selected records without pretending full coverage", async () => {
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
  await userEvent.click(screen.getByText(/条已加载新闻 · 归档状态/));
  expect(screen.getByText(/本次观察仅加载部分安全内容/)).toBeVisible();
});

it("keeps energy observations and provenance available on demand", async () => {
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
  expect(within(energy).getByText(/74.2 美元\/桶/)).not.toBeVisible();
  await userEvent.click(screen.getByText("能源数据与观察"));
  expect(within(energy).getByText(/74.2 美元\/桶/)).toBeVisible();
  expect(within(energy).getByText(/前值日期：2026年11月3日/)).toBeVisible();
  expect(within(energy).getByText(/不证明由上述事件导致/)).toBeVisible();
});

it("keeps selected content stable after source-only refresh and labels report-completion archive fallback", async () => {
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
    within(screen.getByRole("article", { name: "本次事项1" })).getByText(
      "持续时间未知",
    ),
  ).toBeVisible();
  await userEvent.selectOptions(
    screen.getByLabelText("研究日期（仅已加载归档）"),
    "2026-11-05",
  );
  await userEvent.click(
    screen
      .getByRole("article", { name: "历史独立事项" })
      .querySelector("summary")!,
  );
  expect(screen.getByText(/报告完成日期：2026年11月5日/)).toBeVisible();
  expect(
    screen.queryByText("2026年11月5日 08:00", { selector: "time" }),
  ).not.toBeInTheDocument();
});

it.each(["2026-11-02", null])(
  "keeps substantive planned measures without claiming occurrence for %s",
  async (scheduledDate) => {
    const data = context();
    data.dailyResearch!.report!.upcoming.push({
      id: "past-plan",
      title: "央行宣布下月降准0.5个百分点",
      phase: "PLANNED",
      scheduledDate,
      scheduledAt: null,
      sourceIds: [],
      affectedDirectionIds: [],
      watchFor: [],
    });
    render(<EconomicEvents data={data} />);
    const plan = screen.getByRole("article", {
      name: "央行宣布下月降准0.5个百分点",
    });
    expect(
      within(plan.querySelector("summary")!).getByText("发布日期未提供"),
    ).toBeVisible();
    await userEvent.click(plan.querySelector("summary")!);
    expect(
      within(plan).getByText(/原研究计划时间：.*是否发生未确认/),
    ).toBeVisible();
    expect(within(plan).queryByText(/尚未发生/)).not.toBeInTheDocument();
  },
);

it("does not certify event facts through the source-checked badge", async () => {
  const data = context();
  data.dailyResearch!.economicResearch!.observations.events[0].phase =
    "IMPLEMENTED";
  render(<EconomicEvents data={data} />);
  const card = screen.getByRole("article", { name: "本次事项1" });
  expect(
    within(card.querySelector("summary")!).getByText("来源已核对"),
  ).toBeVisible();
  expect(
    within(card.querySelector("summary")!).queryByText(/事实已核实/),
  ).not.toBeInTheDocument();
  await userEvent.click(card.querySelector("summary")!);
  expect(within(card).getByText(/原记录未提供事件事实核验标记/)).toBeVisible();
});

it("prioritizes high-importance teasers with stable ties while retaining archive order", () => {
  const data = context(5);
  data.dailyResearch!.economicResearch!.observations.events.forEach(
    (event, index) => {
      event.importance = index < 3 ? "LOW" : "HIGH";
    },
  );
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

it("shows publication dates in teasers without substituting newer research timestamps", () => {
  render(<EconomicEventTeasers data={context()} onOpenEvent={vi.fn()} />);
  expect(screen.getByText("11-06 09:00")).toBeVisible();
  expect(screen.getByText("发布日期未提供")).toBeVisible();
  expect(screen.queryByText(/2026-11-07|2026-11-05/)).not.toBeInTheDocument();
});

it.each(["", "invalid-date", "2026-02-30T08:00:00+08:00"])(
  "keeps invalid evidence date %j in detail without borrowing it for publication",
  async (asOf) => {
    const data = context();
    data.dailyResearch!.economicResearch!.asOf = asOf;
    const view = render(<EconomicEvents data={data} />);
    const card = screen.getByRole("article", { name: "本次事项1" });
    expect(
      within(card.querySelector("summary")!).getByText("11-06 09:00"),
    ).toBeVisible();
    await userEvent.click(card.querySelector("summary")!);
    expect(within(card).getByText(/日期不可核实/)).toBeVisible();
    expect(
      within(card).queryByText("研究证据日期未提供"),
    ).not.toBeInTheDocument();
    view.rerender(<EconomicEventTeasers data={data} onOpenEvent={vi.fn()} />);
    expect(screen.queryByText(/日期不可核实/)).not.toBeInTheDocument();
  },
);

it("hides empty planned calendar shells and correctly classifies a real British GDP release", () => {
  const data = context();
  const report = data.dailyResearch!.report!;
  report.upcoming.push(
    Object.assign(
      {
        id: "uk-planned",
        title: "英国第三季度GDP",
        phase: "PLANNED" as const,
        scheduledDate: "2026-11-12",
        scheduledAt: null,
        sourceIds: [],
        affectedDirectionIds: [],
        watchFor: [],
      },
      { summary: "英国第三季度GDP" },
    ),
  );
  report.events.push(
    Object.assign(
      {
        ...report.events[0],
        id: "uk-actual",
        title: "英国第三季度GDP环比增长0.3%",
        summary: "英国统计局公布第三季度GDP环比增长0.3%",
      },
      { category: "DATA_RELEASE" },
    ),
  );
  render(<EconomicEvents data={data} />);
  expect(
    screen.queryByRole("article", { name: "英国第三季度GDP" }),
  ).not.toBeInTheDocument();
  expect(
    within(
      screen
        .getByRole("article", { name: "英国第三季度GDP环比增长0.3%" })
        .querySelector("summary")!,
    ).getByText("国际局势"),
  ).toBeVisible();
});

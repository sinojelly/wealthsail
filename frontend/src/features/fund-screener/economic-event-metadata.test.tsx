import { render, screen } from "@testing-library/react";
import type { OpportunityContext } from "../../api/investment";
import { EconomicEvents } from "./components/EconomicEvents";
import { buildEconomicStories } from "./economic-story-model";

function context(): OpportunityContext {
  return {
    state: "READY",
    items: [],
    dailyResearch: {
      methodVersion: "daily-v1",
      state: "READY",
      runId: "frozen-run",
      reportRunId: "frozen-run",
      scheduledFor: null,
      startedAt: null,
      completedAt: "2026-11-07T21:50:00+08:00",
      asOf: "2026-11-07T21:29:00+08:00",
      nextRunAt: null,
      report: {
        summary: "原完整研究",
        coverage: [],
        energy: [],
        upcoming: [],
        sources: [
          {
            id: "title-only",
            title: "原包目录标题",
            url: "https://example.test/a",
            publishedDate: "2026-11-06",
            verification: "UNVERIFIED",
            frozenTitleMatched: true,
            frozenQuoteMatched: false,
          },
          {
            id: "body",
            title: "原包正文",
            url: "https://example.test/b",
            publishedAt: "2026-11-06T23:00:00Z",
            verification: "UNVERIFIED",
            frozenTitleMatched: true,
            frozenQuoteMatched: true,
          },
          {
            id: "absent",
            title: "未冻来源",
            url: "https://example.test/c",
            verification: "UNVERIFIED",
            frozenTitleMatched: false,
            frozenQuoteMatched: false,
          },
        ],
        events: Array.from({ length: 11 }, (_, index) => ({
          id: `event-${index}`,
          title: `机器人事件${index + 1}`,
          summary: "原记录摘要",
          phase: "REPORTED",
          importance: "MEDIUM",
          category: "TECHNOLOGY",
          sourcePublishedAt: "2026-11-06T23:00:00Z",
          sourcePublicationBasis: "SOURCE_PUBLICATION",
          sourceIds: ["title-only", "body", "absent"],
          channels: ["非枚举传导描述"],
          affectedDirectionIds: [],
          scenarios: [],
          watchFor: [],
          unknowns: [],
          marketReaction: "UNVERIFIED",
          reasoningType: "MODEL_CONDITIONAL",
        })),
      },
    },
  } as unknown as OpportunityContext;
}

it("uses closed metadata and source publication without inventing occurrence or verification", () => {
  const story = buildEconomicStories(context(), "2026-11-07")[0];
  expect(story.category).toBe("科技");
  expect(story).toHaveProperty("sourcePublishedAt", "2026-11-06T23:00:00Z");
  expect(story.occurredAt).toBeNull();
  expect(story.sourceStatus.checked).toBe(1);
  expect(story.sources[0].verification).toBe("UNVERIFIED");
});

it("falls back to latest valid linked source publication, never to research time", () => {
  const data = context();
  const event = data.dailyResearch!.report!.events[0] as unknown as Record<
    string,
    unknown
  >;
  delete event.sourcePublishedAt;
  delete event.sourcePublicationBasis;
  const story = buildEconomicStories(data, "2026-11-07")[0];
  expect(story).toHaveProperty("sourcePublishedAt", "2026-11-06T23:00:00Z");
  expect(story.occurredAt).toBeNull();
});

it("shows eleven readable events, separate frozen/title and body counts, and absent-body reason", () => {
  render(<EconomicEvents data={context()} referenceDate="2026-11-07" />);
  expect(screen.getByText(/已加载 11 条可读经济事件/)).toBeInTheDocument();
  expect(screen.queryByText(/尚无本次可读经济观察/)).not.toBeInTheDocument();
  expect(screen.queryAllByText("事件发生时间未单独提供")).toHaveLength(0);
  expect(screen.getAllByText(/冻结资料匹配 2 项/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/原文核对 1 项/).length).toBeGreaterThan(0);
  expect(
    screen.getAllByText(/原包未保存可匹配的标题或正文/).length,
  ).toBeGreaterThan(0);
  expect(screen.getAllByText(/来源发布时间：2026/).length).toBeGreaterThan(0);
});

it("does not claim missing frozen materials when an older report has no match metadata", () => {
  const data = context();
  for (const source of data.dailyResearch!.report!.sources) {
    delete source.frozenTitleMatched;
    delete source.frozenQuoteMatched;
  }
  const story = buildEconomicStories(data, "2026-11-07")[0];
  expect(story.sources[0].frozenTitleMatched).toBeUndefined();
  expect(story.sources[0].frozenQuoteMatched).toBeUndefined();
  render(<EconomicEvents data={data} referenceDate="2026-11-07" />);
  expect(screen.queryByText(/原包未保存可匹配的标题或正文/)).not.toBeInTheDocument();
  expect(screen.getAllByText(/原文核对 0 项/).length).toBeGreaterThan(0);
});

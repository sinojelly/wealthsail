import type { OpportunityContext } from "../../api/investment";
import type { AcceptanceObservation } from "../../api/research-acceptance";
import { buildEconomicStories } from "./economic-story-model";

export function storyContext(): OpportunityContext {
  return {
    state: "READY",
    items: [],
    dailyResearch: {
      methodVersion: "daily-v1",
      state: "FAILED",
      runId: "run-new",
      reportRunId: "run-old",
      scheduledFor: null,
      startedAt: null,
      completedAt: null,
      asOf: null,
      nextRunAt: null,
      report: {
        asOf: "2026-11-05T08:00:00+08:00",
        summary: "历史报告",
        coverage: [],
        sources: [
          {
            id: "s1",
            title: "历史原文",
            url: "https://example.test/archive",
            quote: "旧记录",
            publishedDate: "2026-11-04",
            publishedAt: null,
            retrievedAt: null,
            verification: "SOURCE_CHECKED",
          },
        ],
        events: [
          {
            id: "same-event",
            title: "保留的事实",
            summary: "旧研究的独立事实",
            phase: "REPORTED",
            importance: "HIGH",
            sourceIds: ["s1"],
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
        researchId: "run-new",
        status: "PARTIAL",
        asOf: "2026-11-07T08:00:00+08:00",
        validatedAt: "2026-11-07T08:00:00+08:00",
        recordedAt: "2026-11-07T08:01:00+08:00",
        archiveState: "ARCHIVED",
        archivePending: false,
        acceptedContentComplete: true,
        actionable: false,
        observations: {
          sources: [
            {
              id: "s1",
              title: "本次原文",
              url: "https://example.test/current",
              quote: "新记录",
              verification: "SOURCE_CHECKED",
              publishedDate: "2026-11-06",
            },
          ],
          industryCycleReviews: [],
          valueDirections: [],
          upcoming: [],
          events: [
            {
              id: "same-event",
              title: "保留的事实",
              summary: "本次研究的独立事实",
              phase: "ANNOUNCED",
              sourceIds: ["s1"],
              channels: ["能源与航运"],
              scenarios: [
                {
                  condition: "若供给减少",
                  impact: "成本可能上升",
                  counter: "需求回落可能抵消",
                },
              ],
              watchFor: ["观察实际供给"],
              unknowns: ["持续时间未明"],
            },
          ],
        },
      },
    },
  };
}
const currentEvents = (data: OpportunityContext): AcceptanceObservation[] =>
  data.dailyResearch!.economicResearch!.observations.events;

it("keeps failed-current accepted research, retained research and independent official facts separate", () => {
  const data = storyContext();
  data.events = [
    {
      id: "same-event",
      title: "保留的事实",
      summary: "独立官方记录",
      category: "DATA_RELEASE",
      sourceType: "OFFICIAL_FACT",
      publisher: "统计机构",
      sourceUri: "https://example.test/official",
      publishedAt: "2026-11-03T01:00:00Z",
      retrievedAt: null,
      directionIds: [],
    },
  ];
  const stories = buildEconomicStories(data, "2026-11-07");
  expect(stories).toHaveLength(3);
  expect(new Set(stories.map((story) => story.id)).size).toBe(3);
  expect(
    stories.map((story) => [story.origin, story.researchId, story.summary]),
  ).toEqual([
    ["CURRENT_ACCEPTED", "run-new", "本次研究的独立事实"],
    ["REPORT", "run-old", "旧研究的独立事实"],
    ["OFFICIAL", null, "独立官方记录"],
  ]);
  expect(stories[1].historical).toBe(true);
  expect(stories[0].sourceStatus).toEqual({
    referenced: 1,
    available: 1,
    checked: 1,
    missing: 0,
  });
});

it("never substitutes report or publication dates for occurrence or verifies market reaction", () => {
  const [story] = buildEconomicStories(storyContext(), "2026-11-07");
  expect(story.occurredAt).toBeNull();
  expect(story.researchAsOf).toBe("2026-11-07T08:00:00+08:00");
  expect(story.researchDate).toBe("2026-11-07");
  expect(story.sources[0].publishedAt).toBe("2026-11-06");
  expect(story.marketReaction).toBe("UNVERIFIED");
});

it("deduplicates an exact event with explicit shared evidence only within its research", () => {
  const data = storyContext();
  currentEvents(data).push({
    ...currentEvents(data)[0],
    sourceIds: ["s1", "s2"],
  });
  data.dailyResearch!.economicResearch!.observations.sources.push({
    id: "s2",
    title: "补充原文",
    url: "https://example.test/second",
    verification: "UNVERIFIED",
  });
  const stories = buildEconomicStories(data);
  expect(stories).toHaveLength(2);
  expect(stories[0].sourceIds).toEqual(["s1", "s2"]);
  expect(stories[0].sourceStatus).toEqual({
    referenced: 2,
    available: 2,
    checked: 1,
    missing: 0,
  });
});

it("retains different facts or stages despite equal IDs, titles and aggregate URLs", () => {
  const data = storyContext();
  const event = currentEvents(data)[0];
  currentEvents(data).push(
    { ...event, summary: "另一项事实" },
    { ...event, phase: "IMPLEMENTED" },
    { ...event, id: "other-id" },
  );
  const stories = buildEconomicStories(data);
  expect(stories).toHaveLength(5);
  expect(new Set(stories.map((story) => story.id)).size).toBe(5);
});

it("retains events without explicit evidence and keeps missing and unchecked sources honest", () => {
  const data = storyContext();
  const event = {
    ...currentEvents(data)[0],
    sourceIds: ["missing", "unsafe", "s1"],
  };
  currentEvents(data).splice(0, 1, event);
  data.dailyResearch!.economicResearch!.observations.sources.push({
    id: "unsafe",
    title: "不可用链接",
    url: "javascript:alert(1)",
    verification: "UNVERIFIED",
  });
  currentEvents(data).push(
    { ...event, id: "no-sources", sourceIds: [] },
    { ...event, id: "no-sources", sourceIds: [] },
  );
  const stories = buildEconomicStories(data);
  expect(stories).toHaveLength(4);
  expect(stories[0].sourceStatus).toEqual({
    referenced: 3,
    available: 2,
    checked: 1,
    missing: 1,
  });
  expect(
    stories[0].sources.find((source) => source.id === "unsafe")?.href,
  ).toBeUndefined();
});

it.each(["PENDING", "UNAVAILABLE", "UNKNOWN"] as const)(
  "does not project %s archive payloads as accepted facts",
  (status) => {
    const data = storyContext();
    data.dailyResearch!.economicResearch!.status = status;
    expect(buildEconomicStories(data).map((story) => story.origin)).toEqual([
      "REPORT",
    ]);
  },
);

it("rejects a mismatched delivery or pipeline research identity and retains the saved report", () => {
  const data = storyContext();
  data.dailyResearch!.economicResearch!.researchId = "unrelated-run";
  expect(buildEconomicStories(data)).toHaveLength(1);
  data.dailyResearch!.economicResearch!.researchId = "run-new";
  data.dailyResearch!.pipeline = {
    id: "pipeline",
    state: "RUNNING",
    stage: "MODEL_RUNNING",
    researchRunId: "pipeline-run",
  };
  expect(buildEconomicStories(data)).toHaveLength(1);
});

it("preserves partial content, scheduled dates and explicit associations without inventing lessons", () => {
  const data = storyContext();
  const delivery = data.dailyResearch!.economicResearch!;
  delivery.acceptedContentComplete = false;
  delivery.observations.upcoming.push({
    id: "future",
    title: "待公布事项",
    phase: "PLANNED",
    scheduledDate: "2026-11-09",
    sourceIds: ["s1"],
  });
  currentEvents(data)[0].referenceResolution = {
    version: "research-event-links-v1",
    resolved: [
      {
        namespace: "TOPIC",
        inputKey: "topic-1",
        key: "topic-1",
        method: "EXACT",
      },
    ],
    unresolved: [
      {
        namespace: "FORMAL_DIRECTION",
        key: "missing-topic",
        reasonCode: "UNKNOWN_REFERENCE",
      },
    ],
  };
  currentEvents(data)[0].channels = ["未识别的条件描述"];
  const stories = buildEconomicStories(data);
  expect(stories[0].contentComplete).toBe(false);
  expect(stories[0].lessons).toEqual([]);
  expect(stories[0].associations).toEqual([
    { namespace: "TOPIC", key: "topic-1", status: "RESOLVED" },
    {
      namespace: "FORMAL_DIRECTION",
      key: "missing-topic",
      status: "UNRESOLVED",
    },
  ]);
  expect(stories[1].kind).toBe("UPCOMING");
  expect(stories[1].scheduledAt).toBe("2026-11-09");
  expect(stories[1].occurredAt).toBeNull();
});

it("uses each research date and stable identities when unrelated records reorder", () => {
  const data = storyContext();
  const first = buildEconomicStories(data, "2026-11-08");
  expect(first[0].historical).toBe(true);
  expect(first[1].researchDate).toBe("2026-11-05");
  currentEvents(data).unshift({
    id: "independent",
    title: "独立事件",
    sourceIds: [],
  });
  expect(buildEconomicStories(data, "2026-11-08")[1].id).toBe(first[0].id);
});

it("keeps selected identity stable when source verification, retrieval or supplementary evidence refreshes", () => {
  const data = storyContext();
  const before = buildEconomicStories(data)[0].id;
  const delivery = data.dailyResearch!.economicResearch!;
  delivery.observations.sources[0].verification = "UNVERIFIED";
  delivery.observations.sources[0].retrievedAt = "2026-11-07T10:00:00+08:00";
  delivery.observations.sources.push({
    id: "s2",
    title: "补齐证据",
    url: "https://example.test/new-source",
  });
  currentEvents(data)[0].sourceIds!.push("s2");
  expect(buildEconomicStories(data)[0].id).toBe(before);
});

it("uses report completion as a labelled archive-date fallback without inventing report asOf or occurrence", () => {
  const data = storyContext();
  delete data.dailyResearch!.report!.asOf;
  data.dailyResearch!.reportCompletedAt = "2026-11-04T23:00:00Z";
  const report = buildEconomicStories(data)[1];
  expect(report.researchAsOf).toBeNull();
  expect(report.researchDate).toBe("2026-11-05");
  expect(report.researchDateBasis).toBe("REPORT_COMPLETED");
  expect(report.reportCompletedAt).toBe("2026-11-04T23:00:00Z");
  expect(report.occurredAt).toBeNull();
});

it("keeps explicit declared direction/topic references without treating them as resolved investment links", () => {
  const data = storyContext();
  const event = currentEvents(data)[0];
  event.relatedTopicKeys = ["topic-declared"];
  event.relatedFormalDirectionKeys = ["direction-declared"];
  const [story] = buildEconomicStories(data);
  expect(story.associations).toEqual([
    { namespace: "TOPIC", key: "topic-declared", status: "DECLARED" },
    {
      namespace: "FORMAL_DIRECTION",
      key: "direction-declared",
      status: "DECLARED",
    },
  ]);
});

it("deduplicates equal accepted and formal copies with identical evidence from the same research", () => {
  const data = storyContext();
  const daily = data.dailyResearch!;
  const event = currentEvents(data)[0];
  daily.reportRunId = "run-new";
  daily.report!.sources = [
    {
      id: "s1",
      title: "本次原文",
      url: "https://example.test/current",
      quote: "新记录",
      verification: "SOURCE_CHECKED",
      publishedDate: "2026-11-06",
      publishedAt: null,
      retrievedAt: null,
    },
  ];
  daily.report!.events = [
    {
      ...event,
      id: "same-event",
      title: "保留的事实",
      summary: "本次研究的独立事实",
      phase: "ANNOUNCED",
      importance: "HIGH",
      channels: event.channels!,
      scenarios: event.scenarios!,
      watchFor: event.watchFor!,
      unknowns: event.unknowns!,
      sourceIds: ["s1"],
      affectedDirectionIds: [],
      marketReaction: "UNVERIFIED",
      reasoningType: "MODEL_CONDITIONAL",
      factVerification: "UNVERIFIED",
    },
  ];
  const stories = buildEconomicStories(data);
  expect(stories).toHaveLength(1);
  expect(stories[0].origin).toBe("CURRENT_ACCEPTED");
  expect(stories[0].factVerification).toBe("UNVERIFIED");
  expect(stories[0]).toMatchObject({ importance: "HIGH" });
});

it("handles unknown official category labels without reading object prototype properties", () => {
  const data: OpportunityContext = {
    state: "READY",
    items: [],
    events: [
      {
        id: "official",
        title: "独立事项",
        summary: "事实摘要",
        category: "constructor",
        sourceType: "OFFICIAL_FACT",
        publisher: "官方机构",
        sourceUri: null,
        publishedAt: null,
        retrievedAt: null,
        directionIds: [],
      },
    ],
  };
  expect(buildEconomicStories(data)[0].category).toBe("待分类");
});

it("preserves original fact-verification scope independently of reported phase and checked quotations", () => {
  const data = storyContext();
  const event = data.dailyResearch!.report!.events[0];
  event.phase = "IMPLEMENTED";
  event.factVerification = "UNVERIFIED";
  const stories = buildEconomicStories(data);
  expect(stories[1].phase).toBe("IMPLEMENTED");
  expect(stories[1].sourceStatus.checked).toBe(1);
  expect(stories[1].factVerification).toBe("UNVERIFIED");
  expect(stories[0].factVerification).toBeNull();
  const originalId = stories[1].id;
  delete event.factVerification;
  expect(buildEconomicStories(data)[1].factVerification).toBeNull();
  expect(buildEconomicStories(data)[1].id).toBe(originalId);
});

it("preserves supplied importance and leaves missing or independent official importance unknown", () => {
  const data = storyContext();
  const event = data.dailyResearch!.report!.events[0];
  data.dailyResearch!.report!.events = ["HIGH", "MEDIUM", "LOW", "UNKNOWN"].map(
    (importance) => ({ ...event, id: importance, importance }),
  );
  data.events = [
    {
      id: "official",
      title: "独立官方事实",
      summary: "不提供重要性评级",
      category: "DATA_RELEASE",
      sourceType: "OFFICIAL_FACT",
      publisher: "统计机构",
      sourceUri: null,
      publishedAt: "2026-11-07T09:00:00+08:00",
      retrievedAt: null,
      directionIds: [],
    },
  ];
  expect(buildEconomicStories(data)).toMatchObject([
    { importance: null },
    { importance: "HIGH" },
    { importance: "MEDIUM" },
    { importance: "LOW" },
    { importance: "UNKNOWN" },
    { origin: "OFFICIAL", importance: null, researchDate: null },
  ]);
});

it.each([
  "2026-02-30",
  "2026-02-29",
  "2026-04-31",
  "2026-00-10",
  "2026-13-01",
  "2026-11-00",
  "0000-01-01",
  "1900-02-29",
  "2026-02-30T08:00:00+08:00",
  "2026-02-29T23:00:00Z",
])(
  "rejects impossible research calendar date %s without normalizing it",
  (asOf) => {
    const data = storyContext();
    data.dailyResearch!.economicResearch!.asOf = asOf;
    const [story] = buildEconomicStories(data);
    expect(story.researchDate).toBeNull();
    expect(story.researchDateBasis).toBeNull();
    expect(story.researchAsOf).toBe(asOf);
  },
);

it.each([
  ["2024-02-29", "2024-02-29"],
  ["2000-02-29", "2000-02-29"],
  ["0001-01-01T12:00:00Z", "0001-01-01"],
  ["2026-02-28", "2026-02-28"],
  ["2026-12-31", "2026-12-31"],
  ["2026-02-28T23:00:00Z", "2026-03-01"],
])(
  "retains real calendar date %s and valid Shanghai timestamp conversion",
  (asOf, expected) => {
    const data = storyContext();
    data.dailyResearch!.economicResearch!.asOf = asOf;
    expect(buildEconomicStories(data)[0].researchDate).toBe(expected);
  },
);

it("rejects impossible completion and report-slot dates as archive fallbacks", () => {
  const data = storyContext();
  delete data.dailyResearch!.report!.asOf;
  data.dailyResearch!.reportCompletedAt = "2026-02-30T08:00:00+08:00";
  data.dailyResearch!.reportSlotDate = "2026-02-30";
  const report = buildEconomicStories(data)[1];
  expect(report.researchDate).toBeNull();
  expect(report.researchDateBasis).toBeNull();
  expect(report.reportCompletedAt).toBe("2026-02-30T08:00:00+08:00");
});

it("uses only a valid explicit fallback when the original evidence date is invalid", () => {
  const data = storyContext();
  data.dailyResearch!.report!.asOf = "2026-02-30";
  data.dailyResearch!.reportCompletedAt = "2026-02-28T23:00:00Z";
  const report = buildEconomicStories(data)[1];
  expect(report.researchAsOf).toBe("2026-02-30");
  expect(report.researchDate).toBe("2026-03-01");
  expect(report.researchDateBasis).toBe("REPORT_COMPLETED");
  expect(report.occurredAt).toBeNull();
});

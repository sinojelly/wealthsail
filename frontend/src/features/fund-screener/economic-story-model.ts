import type { OpportunityContext } from "../../api/investment";
import type {
  AcceptanceObservation,
  ResearchAcceptanceOutcome,
} from "../../api/research-acceptance";
import { sourceHref } from "./macro-display";
import {
  classifyEconomicNews,
  isEmptyCalendarStory,
} from "./economic-news-reading";

export type EconomicCategory =
  | "国际局势"
  | "国内经济"
  | "金融市场"
  | "产业与就业"
  | "科技"
  | "货币与政策"
  | "综合要闻";
export interface EconomicSource {
  id: string;
  title: string;
  href?: string;
  quote: string | null;
  publishedAt: string | null;
  retrievedAt: string | null;
  verification: string;
  frozenTitleMatched?: boolean;
  frozenQuoteMatched?: boolean;
}
export interface EconomicStory {
  id: string;
  originalEventId: string | null;
  researchId: string | null;
  origin: "CURRENT_ACCEPTED" | "REPORT" | "OFFICIAL";
  kind: "EVENT" | "UPCOMING";
  title: string;
  summary: string;
  impact?: string | null;
  phase: string | null;
  importance: "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN" | null;
  category: EconomicCategory;
  researchAsOf: string | null;
  researchDate: string | null;
  researchDateBasis: "AS_OF" | "REPORT_COMPLETED" | "REPORT_SLOT" | null;
  reportCompletedAt: string | null;
  reportSlotDate: string | null;
  occurredAt: null;
  sourcePublishedAt: string | null;
  scheduledAt: string | null;
  historical: boolean;
  contentComplete: boolean;
  sourceIds: string[];
  sources: EconomicSource[];
  sourceStatus: {
    referenced: number;
    available: number;
    checked: number;
    missing: number;
  };
  channels: string[];
  scenarios: Array<{ condition: string; impact: string; counter: string }>;
  watchFor: string[];
  unknowns: string[];
  displayQuestions?: string[];
  lessons: Array<{ channel: string; explanation: string }>;
  associations: Array<{
    namespace: string;
    key: string;
    status: "RESOLVED" | "UNRESOLVED" | "DECLARED";
  }>;
  marketReaction: "UNVERIFIED";
  factVerification: "UNVERIFIED" | null;
}

// Exact channel labels only: general mechanisms, never event-specific causal evidence.
const mechanisms: Record<
  string,
  { category: EconomicCategory; explanation: string }
> = {
  能源与航运: {
    category: "国际局势",
    explanation:
      "能源供应或运输成本变化，可能经生产与物流成本传导到物价；需求、库存和替代供应可能改变传导强度。",
  },
  能源成本: {
    category: "国际局势",
    explanation:
      "能源成本变化可能影响企业利润和物价，实际影响取决于成本能否转嫁与需求变化。",
  },
  利率: {
    category: "货币与政策",
    explanation:
      "利率变化可能影响融资成本与资产折现率，信用需求和风险溢价也会影响结果。",
  },
  信用: {
    category: "国内经济",
    explanation:
      "信用条件可能影响融资与支出，但信贷供给增加并不等于企业和居民一定扩大借款。",
  },
  汇率: {
    category: "金融市场",
    explanation:
      "汇率变化可能影响进口成本、出口收入与跨境资金，套期保值和定价货币会改变影响。",
  },
  风险偏好: {
    category: "金融市场",
    explanation:
      "风险偏好变化可能影响资产配置和风险溢价，不能单独确定某项资产的涨跌。",
  },
  产业与就业: {
    category: "产业与就业",
    explanation:
      "企业订单与融资变化可能影响招聘和投资，单个企业的变化不足以证明整个行业趋势。",
  },
  政策: {
    category: "货币与政策",
    explanation:
      "政策可能通过融资、需求或供给影响经济；宣布、实施和效果兑现属于不同阶段。",
  },
  科技创新: {
    category: "科技",
    explanation:
      "技术进展可能经研发、产能和需求影响产业，技术突破、商业落地和盈利兑现需要分别验证。",
  },
};
const categoryCodes: Record<string, EconomicCategory> = {
  UNCLASSIFIED: "综合要闻",
  GEOPOLITICS: "国际局势",
  GEOPOLITICS_POLICY: "国际局势",
  ENERGY_SHIPPING: "国际局势",
  DOMESTIC_GROWTH: "国内经济",
  DATA_RELEASE: "国内经济",
  GLOBAL_EQUITIES: "金融市场",
  FINANCIAL_MARKET: "金融市场",
  INDUSTRY_EMPLOYMENT: "产业与就业",
  INDUSTRY: "产业与就业",
  MONETARY_POLICY: "货币与政策",
  US_RATES: "货币与政策",
  POLICY: "货币与政策",
  TECHNOLOGY: "科技",
  TECH_INNOVATION: "科技",
  AI: "科技",
};
const channelAliases: Record<string, string> = {
  ENERGY_SHIPPING: "能源与航运",
  ENERGY_COST: "能源成本",
  INTEREST_RATE: "利率",
  CREDIT: "信用",
  FX: "汇率",
  RISK_APPETITE: "风险偏好",
  INDUSTRY_EMPLOYMENT: "产业与就业",
  POLICY: "政策",
  AI: "科技创新",
  TECHNOLOGY: "科技创新",
  TECH_INNOVATION: "科技创新",
  技术创新: "科技创新",
  人工智能: "科技创新",
};
type StoryObservation = Omit<AcceptanceObservation, "importance"> & {
  importance?: string;
  affectedDirectionIds?: string[];
  factVerification?: "UNVERIFIED";
  category?: string;
  sourcePublishedAt?: string | null;
  sourcePublicationBasis?: "SOURCE_PUBLICATION" | null;
  whatHappened?: string;
  whyItMatters?: string;
  openQuestions?: string[];
  stillUnknown?: string[];
  displayQuestions?: string[];
};

export function economicCalendarDate(
  raw: string | null | undefined,
): string | null {
  if (!raw) return null;
  const calendar = /^(\d{4})-(\d{2})-(\d{2})(?=$|T)/.exec(raw);
  if (!calendar) return null;
  const [, yearText, monthText, dayText] = calendar;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  // Date.parse normalizes February 30 in both dates and timestamp prefixes.
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > days[month - 1])
    return null;
  if (raw.length === 10) return raw;
  const time = new Date(raw);
  if (!Number.isFinite(time.getTime())) return null;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    })
      .formatToParts(time)
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year.padStart(4, "0")}-${parts.month}-${parts.day}`;
}
function importanceValue(raw: string | undefined): EconomicStory["importance"] {
  return raw === "HIGH" ||
    raw === "MEDIUM" ||
    raw === "LOW" ||
    raw === "UNKNOWN"
    ? raw
    : null;
}

/** Teasers only: supplied importance, then known research date (newest first), then input order.
 * Unknown/missing importance share the last rank; publication dates never substitute for research dates.
 * This selects existing records without mutating the archive or changing any verification/qualification.
 */
export function selectEconomicEventTeasers(
  stories: readonly EconomicStory[],
  limit = 3,
): EconomicStory[] {
  const rank: Record<NonNullable<EconomicStory["importance"]>, number> = {
    HIGH: 0,
    MEDIUM: 1,
    LOW: 2,
    UNKNOWN: 3,
  };
  return stories
    .map((story, order) => ({ story, order }))
    .sort(
      (a, b) =>
        rank[a.story.importance ?? "UNKNOWN"] -
          rank[b.story.importance ?? "UNKNOWN"] ||
        (b.story.researchDate ?? "").localeCompare(
          a.story.researchDate ?? "",
        ) ||
        a.order - b.order,
    )
    .slice(0, limit)
    .map(({ story }) => story);
}
function fingerprint(value: unknown): string {
  const text = JSON.stringify(value);
  let hash = 2166136261;
  for (let index = 0; index < text.length; index++)
    hash = Math.imul(hash ^ text.charCodeAt(index), 16777619);
  return (hash >>> 0).toString(36);
}
function sourceStatus(
  sourceIds: string[],
  sources: EconomicSource[],
): EconomicStory["sourceStatus"] {
  const available = sourceIds.filter((id) =>
    sources.some((source) => source.id === id),
  );
  return {
    referenced: sourceIds.length,
    available: available.length,
    checked: available.filter((id) =>
      sources
        .filter((source) => source.id === id)
        .every(
          (source) =>
            source.verification === "SOURCE_CHECKED" ||
            source.frozenQuoteMatched === true,
        ),
    ).length,
    missing: sourceIds.length - available.length,
  };
}
function sourceProjection(
  source: ResearchAcceptanceOutcome["accepted"]["sources"][number] & {
    frozenTitleMatched?: boolean;
    frozenQuoteMatched?: boolean;
  },
): EconomicSource {
  return {
    id: source.id,
    title: source.title,
    href: sourceHref(source.url),
    quote: source.quote ?? null,
    publishedAt: source.publishedAt ?? source.publishedDate ?? null,
    retrievedAt: source.retrievedAt ?? null,
    verification: source.verification ?? "UNVERIFIED",
    frozenTitleMatched:
      typeof source.frozenTitleMatched === "boolean"
        ? source.frozenTitleMatched
        : undefined,
    frozenQuoteMatched:
      typeof source.frozenQuoteMatched === "boolean"
        ? source.frozenQuoteMatched
        : undefined,
  };
}
function latestPublication(
  values: Array<string | null | undefined>,
): string | null {
  const valid = values.filter(
    (value): value is string => !!economicCalendarDate(value),
  );
  const instant = (value: string) =>
    Date.parse(value.length === 10 ? `${value}T00:00:00+08:00` : value);
  return valid.sort((a, b) => instant(b) - instant(a))[0] ?? null;
}
function factIdentity(story: EconomicStory) {
  return JSON.stringify([
    story.kind,
    story.title,
    story.summary,
    story.phase,
    story.scheduledAt,
    story.channels,
    story.scenarios,
    story.watchFor,
    story.unknowns,
    story.associations,
  ]);
}

/** Read-only projection of loaded material. No requests, model calls, or cross-research association. */
export function buildEconomicStories(
  data: OpportunityContext,
  today?: string,
): EconomicStory[] {
  const stories: EconomicStory[] = [];
  const usedIds = new Set<string>();
  const daily = data.dailyResearch;
  const currentId =
    (daily?.pipeline ? daily.pipeline.researchRunId : daily?.runId) ?? null;
  function append(story: EconomicStory) {
    // An ID, title, shared URL, or similar meaning alone never suffices.
    const duplicate =
      story.researchId &&
      story.originalEventId &&
      stories.find(
        (existing) =>
          existing.origin !== "OFFICIAL" &&
          existing.researchId === story.researchId &&
          existing.originalEventId === story.originalEventId &&
          factIdentity(existing) === factIdentity(story) &&
          existing.sources.some((source) =>
            story.sources.some(
              (other) => JSON.stringify(source) === JSON.stringify(other),
            ),
          ),
      );
    if (duplicate) {
      duplicate.sourceIds = [
        ...new Set([...duplicate.sourceIds, ...story.sourceIds]),
      ];
      duplicate.sources = [
        ...new Map(
          [...duplicate.sources, ...story.sources].map((source) => [
            JSON.stringify(source),
            source,
          ]),
        ).values(),
      ];
      duplicate.sourceStatus = sourceStatus(
        duplicate.sourceIds,
        duplicate.sources,
      );
      duplicate.contentComplete =
        duplicate.contentComplete && story.contentComplete;
      duplicate.factVerification ??= story.factVerification;
      duplicate.importance ??= story.importance;
      return;
    }
    // Source verification and retrieval change on refresh; they are not event identity.
    const base = `${story.origin}:${encodeURIComponent(story.researchId ?? "independent")}:${encodeURIComponent(story.originalEventId ?? "unidentified")}:${fingerprint(factIdentity(story))}`;
    let id = base;
    let occurrence = 1;
    while (usedIds.has(id)) id = `${base}:${++occurrence}`;
    usedIds.add(id);
    stories.push({ ...story, id });
  }
  function addResearch(
    events: StoryObservation[],
    upcoming: StoryObservation[],
    sources: ResearchAcceptanceOutcome["accepted"]["sources"],
    origin: "CURRENT_ACCEPTED" | "REPORT",
    researchId: string | null,
    asOf: string | null,
    contentComplete: boolean,
    retained: boolean,
    reportCompletedAt: string | null = null,
    reportSlot: string | null = null,
  ) {
    const researchDate =
      economicCalendarDate(asOf) ??
      economicCalendarDate(reportCompletedAt) ??
      economicCalendarDate(reportSlot);
    const researchDateBasis: EconomicStory["researchDateBasis"] =
      economicCalendarDate(asOf)
        ? "AS_OF"
        : economicCalendarDate(reportCompletedAt)
          ? "REPORT_COMPLETED"
          : economicCalendarDate(reportSlot)
            ? "REPORT_SLOT"
            : null;
    const allSources = sources.map(sourceProjection);
    function add(event: StoryObservation, kind: EconomicStory["kind"]) {
      if (
        isEmptyCalendarStory(
          event.phase,
          event.title ?? "",
          event.whatHappened ?? event.summary ?? event.thesis ?? "",
        )
      )
        return;
      const sourceIds = [...new Set(event.sourceIds ?? [])];
      const matched = allSources.filter((source) =>
        sourceIds.includes(source.id),
      );
      const channels = event.channels ?? [];
      const lessons = channels.flatMap((channel) => {
        const label = Object.hasOwn(channelAliases, channel)
          ? channelAliases[channel]
          : channel;
        return Object.hasOwn(mechanisms, label)
          ? [{ channel: label, explanation: mechanisms[label].explanation }]
          : [];
      });
      const associations: EconomicStory["associations"] = [
        ...(event.referenceResolution?.resolved ?? []).map((row) => ({
          namespace: row.namespace,
          key: row.key,
          status: "RESOLVED" as const,
        })),
        ...(event.referenceResolution?.unresolved ?? []).map((row) => ({
          namespace: row.namespace,
          key: row.key,
          status: "UNRESOLVED" as const,
        })),
      ];
      const declared = [
        ...(event.relatedTopicKeys ?? []).map((key) => ({
          namespace: "TOPIC",
          key,
        })),
        ...(event.relatedFormalDirectionKeys ?? []).map((key) => ({
          namespace: "FORMAL_DIRECTION",
          key,
        })),
        ...(event.affectedDirectionIds ?? []).map((key) => ({
          namespace: "LEGACY_DIRECTION",
          key,
        })),
      ];
      for (const row of declared) {
        const resolved = event.referenceResolution?.resolved.some(
          (reference) =>
            reference.namespace === row.namespace &&
            (reference.inputKey === row.key || reference.key === row.key),
        );
        const covered = associations.some(
          (reference) =>
            reference.namespace === row.namespace && reference.key === row.key,
        );
        if (!resolved && !covered)
          associations.push({ ...row, status: "DECLARED" });
      }
      append({
        id: "",
        originalEventId: event.id ?? event.key ?? null,
        researchId,
        origin,
        kind,
        title: event.title ?? "未提供事件标题",
        summary:
          event.whatHappened ??
          event.summary ??
          event.thesis ??
          "原研究未提供事实摘要",
        impact: event.whyItMatters ?? null,
        phase: event.phase ?? null,
        importance: importanceValue(event.importance),
        category: classifyEconomicNews(
          event.title ?? "",
          event.whatHappened ?? event.summary ?? event.thesis ?? "",
          matched,
          event.category && Object.hasOwn(categoryCodes, event.category)
            ? categoryCodes[event.category]
            : lessons.length
              ? mechanisms[lessons[0].channel].category
              : "综合要闻",
        ),
        researchAsOf: asOf,
        researchDate,
        researchDateBasis,
        reportCompletedAt,
        reportSlotDate: reportSlot,
        occurredAt: null,
        sourcePublishedAt: latestPublication([
          ...(event.sourcePublicationBasis === "SOURCE_PUBLICATION"
            ? [event.sourcePublishedAt]
            : []),
          ...matched.map((source) => source.publishedAt),
        ]),
        scheduledAt:
          kind === "UPCOMING"
            ? (event.scheduledAt ?? event.scheduledDate ?? null)
            : null,
        historical:
          retained || !!(today && researchDate && researchDate < today),
        contentComplete,
        sourceIds,
        sources: matched,
        sourceStatus: sourceStatus(sourceIds, matched),
        channels,
        scenarios: event.scenarios ?? [],
        watchFor: event.watchFor ?? [],
        unknowns: [
          ...(event.unknowns ?? []),
          ...(event.openQuestions ?? []),
          ...(event.stillUnknown ?? []),
        ],
        displayQuestions: event.displayQuestions,
        lessons,
        associations,
        marketReaction: "UNVERIFIED",
        factVerification: event.factVerification ?? null,
      });
    }
    events.forEach((event) => add(event, "EVENT"));
    upcoming.forEach((event) => add(event, "UPCOMING"));
  }
  const economic = daily?.economicResearch;
  if (
    economic?.version === "economic-research-delivery-v1" &&
    economic.actionable === false &&
    economic.researchId &&
    economic.researchId === currentId &&
    (economic.status === "AVAILABLE" || economic.status === "PARTIAL")
  ) {
    addResearch(
      economic.observations.events,
      economic.observations.upcoming,
      economic.observations.sources,
      "CURRENT_ACCEPTED",
      economic.researchId,
      economic.asOf,
      economic.acceptedContentComplete,
      false,
    );
  }
  if (daily?.report) {
    const reportId =
      daily.reportRunId ?? (daily.state === "READY" ? currentId : null);
    const asOf =
      daily.report.asOf ??
      (reportId && reportId === currentId ? daily.asOf : null);
    addResearch(
      daily.report.events,
      daily.report.upcoming,
      daily.report.sources,
      "REPORT",
      reportId,
      asOf,
      true,
      !reportId || reportId !== currentId || daily.state === "STALE",
      daily.reportCompletedAt ?? null,
      daily.reportSlotDate ?? null,
    );
  }
  for (const event of data.events ?? []) {
    const sources: EconomicSource[] = event.sourceUri
      ? [
          {
            id: event.id,
            title: event.publisher || event.title,
            href: sourceHref(event.sourceUri),
            quote: null,
            publishedAt: event.publishedAt,
            retrievedAt: event.retrievedAt,
            verification: "OFFICIAL_COLLECTION",
          },
        ]
      : [];
    const sourceIds = event.sourceUri ? [event.id] : [];
    append({
      id: "",
      originalEventId: event.id,
      researchId: null,
      origin: "OFFICIAL",
      kind: "EVENT",
      title: event.title,
      summary: event.summary,
      phase: null,
      importance: null,
      category: classifyEconomicNews(
        event.title,
        event.summary,
        sources,
        Object.hasOwn(categoryCodes, event.category)
          ? categoryCodes[event.category]
          : "综合要闻",
      ),
      researchAsOf: null,
      researchDate: null,
      researchDateBasis: null,
      reportCompletedAt: null,
      reportSlotDate: null,
      occurredAt: null,
      sourcePublishedAt: latestPublication([event.publishedAt]),
      scheduledAt: null,
      historical: false,
      contentComplete: true,
      sourceIds,
      sources,
      sourceStatus: sourceStatus(sourceIds, sources),
      channels: [],
      scenarios: [],
      watchFor: [],
      unknowns: [],
      lessons: [],
      associations: event.directionIds.map((key) => ({
        namespace: "DIRECTION",
        key,
        status: "DECLARED",
      })),
      marketReaction: "UNVERIFIED",
      factVerification: null,
    });
  }
  return stories;
}

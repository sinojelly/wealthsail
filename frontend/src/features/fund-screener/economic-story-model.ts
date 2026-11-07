import type { OpportunityContext } from "../../api/investment";
import type {
  AcceptanceObservation,
  ResearchAcceptanceOutcome,
} from "../../api/research-acceptance";
import { sourceHref } from "./macro-display";

export type EconomicCategory =
  | "国际局势"
  | "国内经济"
  | "金融市场"
  | "产业与就业"
  | "科技"
  | "货币与政策"
  | "待分类";
export interface EconomicSource {
  id: string;
  title: string;
  href?: string;
  quote: string | null;
  publishedAt: string | null;
  retrievedAt: string | null;
  verification: string;
}
export interface EconomicStory {
  id: string;
  originalEventId: string | null;
  researchId: string | null;
  origin: "CURRENT_ACCEPTED" | "REPORT" | "OFFICIAL";
  kind: "EVENT" | "UPCOMING";
  title: string;
  summary: string;
  phase: string | null;
  category: EconomicCategory;
  researchAsOf: string | null;
  researchDate: string | null;
  researchDateBasis: "AS_OF" | "REPORT_COMPLETED" | "REPORT_SLOT" | null;
  reportCompletedAt: string | null;
  occurredAt: null;
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
};

function calendarDate(raw: string | null | undefined): string | null {
  if (!raw) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw))
    return Number.isFinite(Date.parse(raw)) ? raw : null;
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
  return `${parts.year}-${parts.month}-${parts.day}`;
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
        .every((source) => source.verification === "SOURCE_CHECKED"),
    ).length,
    missing: sourceIds.length - available.length,
  };
}
function sourceProjection(
  source: ResearchAcceptanceOutcome["accepted"]["sources"][number],
): EconomicSource {
  return {
    id: source.id,
    title: source.title,
    href: sourceHref(source.url),
    quote: source.quote ?? null,
    publishedAt: source.publishedAt ?? source.publishedDate ?? null,
    retrievedAt: source.retrievedAt ?? null,
    verification: source.verification ?? "UNVERIFIED",
  };
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
      calendarDate(asOf) ??
      calendarDate(reportCompletedAt) ??
      calendarDate(reportSlot);
    const researchDateBasis: EconomicStory["researchDateBasis"] = calendarDate(
      asOf,
    )
      ? "AS_OF"
      : calendarDate(reportCompletedAt)
        ? "REPORT_COMPLETED"
        : calendarDate(reportSlot)
          ? "REPORT_SLOT"
          : null;
    const allSources = sources.map(sourceProjection);
    function add(event: StoryObservation, kind: EconomicStory["kind"]) {
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
        summary: event.summary ?? event.thesis ?? "原研究未提供事实摘要",
        phase: event.phase ?? null,
        category: lessons.length
          ? mechanisms[lessons[0].channel].category
          : "待分类",
        researchAsOf: asOf,
        researchDate,
        researchDateBasis,
        reportCompletedAt,
        occurredAt: null,
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
        unknowns: event.unknowns ?? [],
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
      category: Object.hasOwn(categoryCodes, event.category)
        ? categoryCodes[event.category]
        : "待分类",
      researchAsOf: null,
      researchDate: null,
      researchDateBasis: null,
      reportCompletedAt: null,
      occurredAt: null,
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

import { useState } from "react";
import type { OpportunityContext } from "../../../api/investment";
import {
  buildEconomicStories,
  economicCalendarDate,
  selectEconomicEventTeasers,
  type EconomicCategory,
  type EconomicStory,
} from "../economic-story-model";
import {
  metricValue,
  sourceHref,
  timeLabel,
  unitLabel,
} from "../macro-display";
import { readingText, sourceTitle } from "../research-reading";
import "../economic-events.css";

export interface EconomicEventsProps {
  data: OpportunityContext;
  selectedEventId?: string;
  onSelectEvent?: (id: string) => void;
}
export interface EconomicEventTeasersProps {
  data: OpportunityContext;
  onOpenEvent: (id: string) => void;
}
const categories: Array<EconomicCategory | "全部"> = [
  "全部",
  "国际局势",
  "国内经济",
  "金融市场",
  "产业与就业",
  "科技",
  "货币与政策",
  "待分类",
];
const phases: Record<string, string> = {
  PLANNED: "计划中",
  ANNOUNCED: "已宣布",
  IMPLEMENTED: "已实施",
  REPORTED: "报道线索",
  OPINION: "观点",
};
function todayInShanghai() {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    })
      .formatToParts(new Date())
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}
function originLabel(story: EconomicStory) {
  return story.origin === "OFFICIAL"
    ? "独立官方资料"
    : story.origin === "CURRENT_ACCEPTED"
      ? `本次安全经济观察${story.historical ? " · 历史日期" : ""}`
      : story.historical
        ? "历史有效报告"
        : "已保存研究报告";
}
function reportHref(reportRunId: string | null) {
  const params = new URLSearchParams(window.location.search);
  params.set("researchTab", "macro");
  params.set("researchOpen", "1");
  if (reportRunId) params.set("reportRunId", reportRunId);
  else params.delete("reportRunId");
  return `?${params}#latest-successful-daily-report`;
}
function storyTimeLabel(raw: string | null) {
  return raw !== null && !economicCalendarDate(raw)
    ? `日期不可核实（原记录：${readingText(raw, 180)}）`
    : timeLabel(raw);
}
function StoryDates({ story }: { story: EconomicStory }) {
  return (
    <div className="economic-event-dates">
      <span>事件发生时间未单独提供</span>
      {story.researchAsOf !== null ? (
        <span>
          研究日期：
          {economicCalendarDate(story.researchAsOf) ? (
            <time dateTime={story.researchAsOf}>
              {timeLabel(story.researchAsOf)}
            </time>
          ) : (
            storyTimeLabel(story.researchAsOf)
          )}
        </span>
      ) : (
        <span>
          {story.origin === "OFFICIAL"
            ? "独立发布，不隶属研究日期"
            : "研究证据日期未提供"}
        </span>
      )}
      {story.researchDateBasis !== "AS_OF" &&
        story.reportCompletedAt !== null && (
          <span>
            报告完成日期：{storyTimeLabel(story.reportCompletedAt)}
            {story.researchDateBasis === "REPORT_COMPLETED" &&
              "（仅用于归档日期浏览）"}
          </span>
        )}
      {story.researchDateBasis !== "AS_OF" &&
        story.researchDateBasis !== "REPORT_COMPLETED" &&
        story.reportSlotDate !== null && (
          <span>
            报告所属日期：{storyTimeLabel(story.reportSlotDate)}
            {story.researchDateBasis === "REPORT_SLOT" &&
              "（仅用于归档日期浏览）"}
          </span>
        )}
      {story.kind === "UPCOMING" && (
        <span>
          原研究计划时间：{storyTimeLabel(story.scheduledAt)}（是否发生未确认）
        </span>
      )}
    </div>
  );
}
function StoryCard({
  story,
  selected,
  onSelect,
}: {
  story: EconomicStory;
  selected: boolean;
  onSelect?: (id: string) => void;
}) {
  const count = story.sourceStatus;
  return (
    <article
      className="economic-event-card"
      aria-label={story.title}
      data-event-id={story.id}
    >
      <div className="economic-event-tags">
        <span>{story.category}</span>
        <span>{originLabel(story)}</span>
        <span>
          {story.phase && Object.hasOwn(phases, story.phase)
            ? phases[story.phase]
            : "事件阶段未提供"}
        </span>
      </div>
      <h4>{readingText(story.title, 220)}</h4>
      <StoryDates story={story} />
      <p className="economic-event-fact">
        <strong>
          {story.kind === "UPCOMING"
            ? "原研究日程："
            : "发生了什么 · 原记录摘要："}
        </strong>
        {readingText(story.summary, 700)}
      </p>
      <p className="economic-event-source-count">
        引用来源 {count.referenced} 项 · 可读 {count.available} 项 · 原文核对{" "}
        {count.checked} 项 · 缺失 {count.missing} 项
      </p>
      <p className="economic-event-gap">事件事实未独立核验</p>
      <p className="economic-event-gap">尚无可靠市场反应数据</p>
      {story.scenarios[0] ? (
        <p>
          <strong>为什么关注 · 条件推演：</strong>
          {readingText(story.scenarios[0].condition, 100)} →{" "}
          {readingText(story.scenarios[0].impact, 120)}
        </p>
      ) : (
        <p className="economic-event-muted">
          原记录未提供完整的条件与影响组合，传导仍待补充。
        </p>
      )}
      <details
        open={selected || undefined}
        onToggle={(event) => {
          if (event.currentTarget.open && !selected) onSelect?.(story.id);
        }}
      >
        <summary>阅读传导、情景与来源</summary>
        <div className="economic-event-detail">
          <p className="economic-event-muted">
            {story.factVerification === "UNVERIFIED"
              ? "原事件事实核验标记：UNVERIFIED（未独立核验）。事件阶段与引用原文核对属于不同范围。"
              : "原记录未提供事件事实核验标记；来源采集或原文核对不代表事件事实已获独立认证。"}
          </p>
          {!story.contentComplete && (
            <p role="status">
              本项仅加载部分安全内容，不能据此判断全份研究覆盖。
            </p>
          )}
          <section aria-label="条件传导">
            <h5>可能怎样传导 · 原研究条件推演</h5>
            {story.channels.length ? (
              <ul>
                {story.channels.map((channel, index) => (
                  <li key={index}>{readingText(channel, 1200)}</li>
                ))}
              </ul>
            ) : (
              <p>原研究未提供传导渠道。</p>
            )}
          </section>
          <section aria-label="后续情景">
            <h5>后续情景与反方条件</h5>
            {story.scenarios.length ? (
              story.scenarios.map((scenario, index) => (
                <dl className="economic-event-scenario" key={index}>
                  <dt>如果</dt>
                  <dd>{readingText(scenario.condition, 1200)}</dd>
                  <dt>可能影响</dt>
                  <dd>{readingText(scenario.impact, 1200)}</dd>
                  <dt>反方条件</dt>
                  <dd>{readingText(scenario.counter, 1200)}</dd>
                </dl>
              ))
            ) : (
              <p>原研究未提供完整情景。</p>
            )}
          </section>
          <section aria-label="后续观察">
            <h5>接下来观察什么</h5>
            {story.watchFor.length ? (
              <ul>
                {story.watchFor.map((item, index) => (
                  <li key={index}>{readingText(item, 1200)}</li>
                ))}
              </ul>
            ) : (
              <p>原研究未提供观察条件。</p>
            )}
          </section>
          <section aria-label="未知事项">
            <h5>仍不知道什么</h5>
            {story.unknowns.length ? (
              <ul>
                {story.unknowns.map((item, index) => (
                  <li key={index}>{readingText(item, 1200)}</li>
                ))}
              </ul>
            ) : (
              <p>原研究未单列未知项，不代表不存在不确定性。</p>
            )}
          </section>
          <section aria-label="经济知识">
            <h5>经济小课堂 · 通用机制</h5>
            {story.lessons.length ? (
              <>
                <p>仅对应本项已有渠道的通用解释，不代表本事件已证实该传导。</p>
                {story.lessons.map((lesson, index) => (
                  <p key={index}>
                    <strong>{lesson.channel}：</strong>
                    {lesson.explanation}
                  </p>
                ))}
              </>
            ) : (
              <p>本项没有可精确对应通用机制字典的渠道，暂缺知识说明。</p>
            )}
          </section>
          <section aria-label="原文来源">
            <h5>来源与核对范围</h5>
            <p>
              原文引用核对不等于事实或因果认证；条件推演仍需证据验证。可读项数指已加载的来源记录，不保证链接可用。
            </p>
            {story.sources.length ? (
              <ul className="economic-event-sources">
                {story.sources.map((source, index) => (
                  <li key={index}>
                    {source.href ? (
                      <a
                        href={source.href}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {sourceTitle(source.title)}
                      </a>
                    ) : (
                      <span>
                        {sourceTitle(source.title)}（链接无效或未提供）
                      </span>
                    )}
                    <p>
                      {source.verification === "SOURCE_CHECKED"
                        ? "原文引用已核对"
                        : source.verification === "OFFICIAL_COLLECTION"
                          ? "官方资料采集记录，原文引用未单独核对"
                          : "原文引用尚未核对"}
                    </p>
                    <p>
                      来源发布时间：{storyTimeLabel(source.publishedAt)}
                      ；采集时间：
                      {storyTimeLabel(source.retrievedAt)}
                    </p>
                    {source.quote && (
                      <blockquote>{readingText(source.quote, 1400)}</blockquote>
                    )}
                    <small>原始来源 ID：{source.id}</small>
                  </li>
                ))}
              </ul>
            ) : (
              <p>本项暂无可读来源。</p>
            )}
            {count.missing > 0 && (
              <p>
                缺少引用来源：
                {story.sourceIds
                  .filter(
                    (id) => !story.sources.some((source) => source.id === id),
                  )
                  .join("、")}
              </p>
            )}
          </section>
          {story.associations.length > 0 && (
            <section aria-label="原研究关联">
              <h5>本份研究的显式关联</h5>
              <p>仅保留原研究记录的关联，不构成投资资格或跨日进展。</p>
              <ul>
                {story.associations.map((row, index) => (
                  <li key={index}>
                    {row.namespace} / {row.key} ·{" "}
                    {row.status === "RESOLVED"
                      ? "原研究引用已解析"
                      : row.status === "UNRESOLVED"
                        ? "引用未解析"
                        : "仅声明关联"}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <div className="economic-event-record">
            <p>原事件 ID：{story.originalEventId ?? "未提供"}</p>
            <p>原研究 ID：{story.researchId ?? "独立公开记录，无研究 ID"}</p>
            {story.origin === "REPORT" && (
              <>
                <a href={reportHref(story.researchId)}>查看本份报告出处</a>
                <p>报告出处不表示已核实的事件进展。</p>
              </>
            )}
          </div>
        </div>
      </details>
    </article>
  );
}
function DeliveryStatus({ data }: { data: OpportunityContext }) {
  const daily = data.dailyResearch;
  const currentId = daily?.pipeline
    ? daily.pipeline.researchRunId
    : daily?.runId;
  const economic = daily?.economicResearch;
  const matching =
    economic?.version === "economic-research-delivery-v1" &&
    economic.actionable === false &&
    economic.researchId &&
    economic.researchId === currentId;
  const state = daily?.pipeline?.state ?? daily?.state;
  const labels = {
    AVAILABLE: "本次经济观察已有可读资料。",
    PARTIAL: "本次经济观察包含待核实内容，已安全接受的记录可以阅读。",
    PENDING: "本次经济观察待归档，尚不能作为可读事件。",
    UNAVAILABLE: "本次经济观察暂无可读成果。",
    UNKNOWN: "本次经济观察交付状态未知。",
  };
  return (
    <div className="economic-event-status" role="status">
      {state === "RUNNING" && (
        <p>本次研究正在执行，已保留的报告和独立公开资料仍可阅读。</p>
      )}
      {state === "FAILED" && (
        <p>本次正式投资报告未通过；安全经济观察与保留历史报告分别显示。</p>
      )}
      {matching ? (
        <>
          <p>{labels[economic.status]}</p>
          {!economic.acceptedContentComplete &&
            (economic.status === "AVAILABLE" ||
              economic.status === "PARTIAL") && (
              <p>
                本次观察仅加载部分安全内容，不代表全部新闻或全部研究已覆盖。
              </p>
            )}
          {economic.archivePending && economic.status !== "PENDING" && (
            <p>其余归档内容仍在恢复中。</p>
          )}
        </>
      ) : (
        <p>
          {economic?.researchId
            ? "经济观察身份与本次执行未对应，未作为本次资料显示。"
            : "尚无本次可读经济观察，以下只展示已加载资料。"}
        </p>
      )}
    </div>
  );
}
function EnergyObservations({ data }: { data: OpportunityContext }) {
  const energy = data.dailyResearch?.report?.energy ?? [];
  return (
    <section className="economic-energy" aria-label="独立能源观察">
      <h4>独立能源观察</h4>
      <p>
        以下程序观察与事件独立，不证明由上述事件导致，也不充当事件发生时间。
      </p>
      {energy.length ? (
        <ul>
          {energy.map((item, index) => (
            <li key={`${item.id}:${index}`}>
              <strong>
                {item.label}：{metricValue(item.value, 4)}{" "}
                {unitLabel(item.unit)}
              </strong>
              <p>
                观察日期：{timeLabel(item.asOf)} ·{" "}
                {item.state === "STALE" ? "旧期观察，需更新" : "已采集观察"}
              </p>
              <p>
                前值：{metricValue(item.previousValue, 4)}；前值日期：
                {timeLabel(item.previousDate)}；变化：
                {metricValue(item.change, 4)} {unitLabel(item.unit)}（
                {metricValue(item.changePct)}%）
              </p>
              <p>
                采集时间：{timeLabel(item.retrievedAt)}
                ；来源发布时间未单独提供。
              </p>
              {sourceHref(item.sourceUri) && (
                <a
                  href={sourceHref(item.sourceUri)}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  查看能源数据来源
                </a>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p>本页已加载报告未提供能源观察数值。</p>
      )}
      <p>股票、汇率、黄金等资产尚缺与本事件对应的可靠市场反应证据。</p>
    </section>
  );
}
export function EconomicEvents({
  data,
  selectedEventId,
  onSelectEvent,
}: EconomicEventsProps) {
  const stories = buildEconomicStories(data, todayInShanghai());
  const [category, setCategory] = useState<EconomicCategory | "全部">("全部");
  const [researchDate, setResearchDate] = useState("ALL");
  const [showAll, setShowAll] = useState(false);
  const dates = [
    ...new Set(
      stories.flatMap((story) =>
        story.researchDate ? [story.researchDate] : [],
      ),
    ),
  ]
    .sort()
    .reverse();
  const filtered = stories.filter(
    (story) =>
      (category === "全部" || story.category === category) &&
      (researchDate === "ALL" || story.researchDate === researchDate),
  );
  let visible = showAll ? filtered : filtered.slice(0, 8);
  const selected = filtered.find((story) => story.id === selectedEventId);
  if (!showAll && selected && !visible.includes(selected))
    visible = [...visible.slice(0, 7), selected];
  return (
    <section className="economic-events" aria-label="经济事件阅读">
      <header>
        <p className="economic-events-eyebrow">世界与经济观察</p>
        <h3>读懂事件，分清事实与条件推演</h3>
        <p>按原研究分别归档；跨研究的相同事件 ID 仍独立展示。</p>
      </header>
      <DeliveryStatus data={data} />
      <div className="economic-event-filters">
        <div role="group" aria-label="事件阅读分类">
          {categories.map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={category === value}
              onClick={() => {
                setCategory(value);
                setShowAll(false);
              }}
            >
              {value}
            </button>
          ))}
        </div>
        <label>
          研究日期（仅已加载归档）
          <select
            aria-label="研究日期（仅已加载归档）"
            value={researchDate}
            onChange={(event) => {
              setResearchDate(event.target.value);
              setShowAll(false);
            }}
          >
            <option value="ALL">全部已加载日期及独立发布</option>
            {dates.map((date) => (
              <option key={date} value={date}>
                {date}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="economic-event-muted">
        仅浏览本页已加载的研究日期；缺研究证据日期时可按已提供的报告完成日或所属日浏览，并在卡片单独标注。不代表完整历史检索；独立发布不冒充研究日期。分类来自显式类别或可对应的渠道，其余保留待分类。
      </p>
      {selectedEventId &&
        !stories.some((story) => story.id === selectedEventId) && (
          <p role="status">所选事件不在已加载资料中，请从现有记录重新选择。</p>
        )}
      {!filtered.length && (
        <p className="economic-event-empty">
          {stories.length
            ? "当前筛选没有可读事件，可调整分类或研究日期。"
            : "暂无已加载的可读经济事件；资料未归档或尚未提供，不代表世界没有新事件。"}
        </p>
      )}
      <div className="economic-event-list">
        {visible.map((story) => (
          <StoryCard
            key={story.id}
            story={story}
            selected={story.id === selectedEventId}
            onSelect={onSelectEvent}
          />
        ))}
      </div>
      {!showAll && filtered.length > visible.length && (
        <button
          className="economic-event-more"
          type="button"
          onClick={() => setShowAll(true)}
        >
          查看更多事件（共 {filtered.length} 条已加载记录）
        </button>
      )}
      <EnergyObservations data={data} />
    </section>
  );
}
export function EconomicEventTeasers({
  data,
  onOpenEvent,
}: EconomicEventTeasersProps) {
  const stories = selectEconomicEventTeasers(
    buildEconomicStories(data, todayInShanghai()),
  );
  return (
    <section className="economic-event-teasers" aria-label="经济事件简讯">
      <h3>经济事件简讯</h3>
      {stories.length ? (
        <ul>
          {stories.map((story) => (
            <li key={story.id}>
              <span className="economic-event-muted">
                {originLabel(story)} ·{" "}
                {story.researchDate ??
                  (story.origin === "OFFICIAL"
                    ? "独立公开记录"
                    : [
                          story.researchAsOf,
                          story.reportCompletedAt,
                          story.reportSlotDate,
                        ].some((date) => date !== null)
                      ? "日期不可核实"
                      : "研究日期未提供")}
              </span>
              <h4>{readingText(story.title, 100)}</h4>
              <p>{readingText(story.summary, 140)}</p>
              <p className="economic-event-muted">事件事实未独立核验</p>
              <p className="economic-event-muted">
                {story.sources[0]
                  ? `${sourceTitle(story.sources[0].title, 70)} · 来源发布 ${storyTimeLabel(story.sources[0].publishedAt)}`
                  : "来源尚未提供"}
              </p>
              <button
                type="button"
                aria-label={`阅读事件：${story.title}`}
                onClick={() => onOpenEvent(story.id)}
              >
                阅读事件 →
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p>暂无已加载的事件简讯。</p>
      )}
    </section>
  );
}

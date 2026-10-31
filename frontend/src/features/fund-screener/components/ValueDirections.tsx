import FundNavTrendChart from "./FundNavTrendChart";
import {
  bestDirectionPriority,
  buyZoneStatus,
  buyZoneClassName,
  comparePriority,
} from "../opportunity-display";
import { useState } from "react";
import type {
  ActionableZoneResult,
  DailyResearch,
  PlanTrack,
  ValueDirection,
} from "../../../api/investment";
import {
  sourceHref,
  timeLabel,
  monthLabel,
  metricValue,
  macroUnit,
} from "../macro-display";
import {
  fundDisplayName,
  planPrice,
  planTimeBasisNote,
  planUnit,
} from "../plan-display";
import { readingText, sourceTitle } from "../research-reading";
import { researchScheduleLabel } from "../research-schedule-display";
import ActionablePlan, { ActionablePlanEvidence } from "./ActionablePlan";
import { studyMetricLabel } from "./study-display";
import PlanRevision from "./PlanRevision";
import { useLatestFundQuote } from "../latest-quote-resource";
import { FundWatchQuote } from "./PriceWatchSummary";
import EstimatedProfitRange from "./EstimatedProfitRange";
import PlanPriceRefresh from "./PlanPriceRefresh";
import { IndustryCycleBadge, IndustryCycleWarning } from "./IndustryCycle";

export interface DirectionTrackInput {
  reportRunId: string;
  valueDirectionKey: string;
  subjectKind: ActionableZoneResult["subjectKind"];
  subjectId: string;
  planTrack: PlanTrack;
}
const labels = {
  LEFT_VALUE: "左侧低吸观察",
  RIGHT_TREND: "右侧趋势观察",
  TAKE_PROFIT: "止盈复核",
  WATCH: "继续观察",
};

type MacroFact = NonNullable<ValueDirection["macroEvidence"]>[number];
function macroLabel(row: MacroFact) {
  const known = studyMetricLabel(row.code);
  if (known !== "其他待核查指标") return known;
  const extra: Record<string, string> = {
    M1_YOY: "M1同比",
    TSF_STOCK_YOY: "社融存量同比",
    DXY: "美元指数",
    USD_CNY: "美元兑人民币",
  };
  if (extra[row.code]) return extra[row.code];
  const translated = readingText(row.code);
  if (translated !== row.code) return translated;
  const house =
    /^HOUSE_(HAIKOU|SANYA|SHANGHAI|SHENZHEN)_(NEW|SECOND)_MOM$/.exec(row.code);
  if (house)
    return `${({ HAIKOU: "海口", SANYA: "三亚", SHANGHAI: "上海", SHENZHEN: "深圳" } as Record<string, string>)[house[1]]}${house[2] === "NEW" ? "新房" : "二手房"}环比`;
  return row.label && row.label !== row.code
    ? readingText(row.label, 80)
    : "其他宏观观察";
}
function MacroReferences({ rows }: { rows?: ValueDirection["macroEvidence"] }) {
  if (!rows?.length) return null;
  return (
    <p className="op-value-macro">
      宏观参考：
      {rows
        .map((row) => {
          const label = macroLabel(row);
          const numeric =
            (typeof row.value === "number" ||
              (typeof row.value === "string" && row.value.trim() !== "")) &&
            Number.isFinite(Number(row.value))
              ? Number(row.value)
              : null;
          const unit = macroUnit({ code: row.code, unit: row.unit ?? "" });
          const period = row.referencePeriod
            ? monthLabel(row.referencePeriod)
            : timeLabel(row.referenceDate ?? row.asOf);
          return `${label} ${numeric === null ? "未知" : `${metricValue(numeric, 2)} ${unit}`}（${period}）`;
        })
        .join("；")}
    </p>
  );
}

function DirectionSignals({
  rows,
  sources,
  risk = false,
}: {
  rows?: ValueDirection["catalysts"];
  sources: NonNullable<DailyResearch["report"]>["sources"];
  risk?: boolean;
}) {
  if (!rows?.length) return null;
  return (
    <section
      className={risk ? "op-value-risks" : "op-value-catalysts"}
      aria-label={risk ? "关键风险" : "机会催化"}
    >
      {rows.slice(0, 2).map((row, index) => {
        const seen = new Set<string>();
        const references = [...new Set(row.sourceIds)].flatMap((id) => {
          const source = sources.find((item) => item.id === id);
          const href = source && sourceHref(source.url);
          if (!source || !href || seen.has(href)) return [];
          seen.add(href);
          return [{ source, href }];
        });
        return (
          <p key={index}>
            <strong>{risk ? "风险" : "催化"}：</strong>
            {readingText(row.summary, 150)}
            <span className="op-inline-sources">
              {references.map(({ source, href }, sourceIndex) => (
                <a
                  key={source.id}
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={`${risk ? "风险" : "催化"}来源：${sourceTitle(source.title, 100)}`}
                >
                  [来源{sourceIndex + 1}]
                </a>
              ))}
            </span>
          </p>
        );
      })}
    </section>
  );
}

function FundPlan({
  plan,
  direction,
  sources,
  reportRunId,
  onTrack,
}: {
  plan: ActionableZoneResult;
  direction: ValueDirection;
  sources: NonNullable<DailyResearch["report"]>["sources"];
  reportRunId?: string | null;
  onTrack?: (input: DirectionTrackInput) => Promise<void>;
}) {
  const [state, setState] = useState<"idle" | "pending" | "saved">("idle");
  const [error, setError] = useState("");
  const selected = plan.selectedTrack ?? plan.track;
  const track = plan.tracks.find((item) => item.track === selected);
  const price = (value: number | string | null | undefined) =>
    planPrice(value, plan);
  const status = buyZoneStatus(plan, track);
  const inSellZone = plan.inSellZone === true || track?.inSellZone === true;
  const canTrack =
    !!reportRunId &&
    !!onTrack &&
    !!selected &&
    !!track &&
    track.state !== "INSUFFICIENT" &&
    track.entryZoneMin != null &&
    track.entryZoneMax != null;
  const code = plan.fundCode || plan.subjectId;
  const latest = useLatestFundQuote(
    plan.priceBasis === "UNIT_NAV" ? code : undefined,
  );
  const liveConfirmed = latest.data?.confirmedNav;
  const confirmed =
    liveConfirmed?.value != null &&
    liveConfirmed.asOf &&
    liveConfirmed.asOf >= (plan.asOf?.slice(0, 10) ?? "")
      ? liveConfirmed
      : null;
  const displayedPrice = confirmed?.value ?? plan.currentPrice;
  const displayedAsOf = confirmed?.asOf ?? plan.asOf;
  const updatedConfirmed =
    !!confirmed &&
    (confirmed.asOf!.slice(0, 10) > (plan.asOf?.slice(0, 10) ?? "") ||
      Number(confirmed.value) !== Number(plan.currentPrice));
  const updatedEstimate = !!latest.data?.currentEstimate;
  const displayedChange = confirmed
    ? confirmed.changePct
    : plan.currentChangePct;
  const navDay = displayedAsOf?.match(/^\d{4}-(\d{2})-(\d{2})(?:$|T)/);
  const dailyChange =
    plan.priceBasis === "UNIT_NAV" &&
    displayedPrice != null &&
    displayedChange != null &&
    String(displayedChange).trim() !== "" &&
    Number.isFinite(Number(displayedChange))
      ? Number(displayedChange)
      : null;
  async function save() {
    if (!canTrack || !reportRunId || !onTrack || !selected || state !== "idle")
      return;
    setState("pending");
    setError("");
    try {
      await onTrack({
        reportRunId,
        valueDirectionKey: direction.key,
        subjectKind: plan.subjectKind,
        subjectId: plan.subjectId,
        planTrack: selected,
      });
      setState("saved");
    } catch (failure) {
      setState("idle");
      setError(
        failure instanceof Error ? failure.message : "保存失败，请重试。",
      );
    }
  }
  return (
    <article
      className="op-value-fund"
      aria-label={`${fundDisplayName(plan.label, code)}买卖计划`}
    >
      <h4>
        {readingText(fundDisplayName(plan.label, code), 70)} <span>{code}</span>
        {plan.subscriptionLabel && (
          <span className="op-subscription-restricted">
            {plan.subscriptionLabel}
          </span>
        )}
      </h4>
      <div className="op-value-fund-layout">
        <div className="op-value-price-column">
          <p className="op-value-current">
            {plan.priceBasis === "UNIT_NAV" ? "最新确认净值" : "最新实际点位"}：
            <span className="op-price-amount">
              <strong>{price(displayedPrice)}</strong> {planUnit(plan.unit)}
              {dailyChange !== null && (
                <span
                  className={`op-nav-change op-nav-change-${dailyChange > 0 ? "up" : dailyChange < 0 ? "down" : "flat"}`}
                  aria-label="净值当日涨跌幅"
                  title="对应所示净值日期的当日涨跌幅"
                >
                  {dailyChange > 0 ? "+" : ""}
                  {dailyChange.toFixed(2)}%
                </span>
              )}
              {plan.priceBasis === "UNIT_NAV" && navDay && (
                <>
                  {" "}
                  <time
                    dateTime={displayedAsOf ?? undefined}
                    title={`净值日期：${timeLabel(displayedAsOf)}`}
                  >
                    ({navDay[1]}.{navDay[2]})
                  </time>
                </>
              )}
            </span>
            <span className={buyZoneClassName(status)}>
              {confirmed || latest.data?.currentEstimate ? "研究时：" : ""}
              {status.text}
            </span>
          </p>
          <IndustryCycleWarning
            cycle={plan.industryCycle ?? direction.industryCycle}
            warning={plan.industryRiskWarning ?? direction.industryRiskWarning}
            sources={sources}
          />
          {plan.priceBasis === "UNIT_NAV" && latest.data?.currentEstimate && (
            <FundWatchQuote
              row={latest.data.currentEstimate}
              label="基金估算净值"
              estimate
            />
          )}
          {plan.priceBasis === "UNIT_NAV" && latest.loading && !latest.data && (
            <small>正在核对最新行情…</small>
          )}
          {plan.priceBasis === "UNIT_NAV" && latest.failed && (
            <small>最新行情暂不可用，保留已披露净值。</small>
          )}
          {track ? (
            <dl className="op-value-prices">
              <div>
                <dt>建议买入区间</dt>
                <dd>
                  {price(track.entryZoneMin)}–{price(track.entryZoneMax)}
                </dd>
              </div>
              <div>
                <dt>建议卖出区间</dt>
                <dd className={inSellZone ? "op-sell-zone-active" : undefined}>
                  {price(track.sellZoneMin ?? track.takeProfitT1)}–
                  {price(track.sellZoneMax ?? track.takeProfitT2)}
                </dd>
                <small>T1 / T2 分批止盈</small>
              </div>
              <div className="op-value-stop">
                <dt>止损参考</dt>
                <dd>{price(track.stopLossPrice)}</dd>
              </div>
              <EstimatedProfitRange
                track={track}
                priceRefresh={plan.priceRefresh}
              />
            </dl>
          ) : (
            <p>等待真实价格依据</p>
          )}
          {plan.priceRefresh ? (
            <PlanPriceRefresh plan={plan} />
          ) : (
            <small>
              {timeLabel(plan.asOf)} ·{" "}
              {selected === "LEFT_VALUE" ? "左侧" : "右侧"}
              研究坐标
            </small>
          )}
          {planTimeBasisNote(plan) && (
            <p className="op-plan-time-basis">{planTimeBasisNote(plan)}</p>
          )}
          <PlanRevision plan={plan} />
          {track?.priceState === "PARTIAL" && (
            <p className="op-plan-time-basis">
              买卖依据尚未完整，当前区间仅供观察，请等待历史样本与风险尺度补齐。
            </p>
          )}
          {inSellZone && (
            <p className="op-sell-zone-conclusion">
              {plan.priceRefresh && plan.priceRefresh.state !== "READY"
                ? "已触发卖出观察条件；当前买卖区间依据尚未完整，仅供参考，请核对最新净值与价格依据后再评估。"
                : updatedConfirmed
                  ? "研究时已进入建议卖出区间；最新净值已更新，请按当前价格重新评估。"
                  : updatedEstimate
                    ? "研究时已进入建议卖出区间；当前估值仅供参考，请核对最新行情后重新评估。"
                    : "当前净值已进入建议卖出区间，持仓可分批止盈兑现；空仓请等待回落至建议买入区间再评估。"}
            </p>
          )}
          {!!plan.calibrationRuleKeys?.length && plan.calibrationNote && (
            <p>复盘校准：{readingText(plan.calibrationNote, 140)}</p>
          )}
          {plan.subjectKind === "index" && (
            <small>指数点位用于指数研究，基金提醒须使用基金净值。</small>
          )}
          {canTrack && (
            <button
              type="button"
              disabled={state !== "idle"}
              aria-label={`${state === "saved" ? "已跟踪" : "跟踪该方向与买卖点"} · ${code}`}
              onClick={() => void save()}
            >
              {state === "saved"
                ? "已跟踪"
                : state === "pending"
                  ? "保存中…"
                  : "跟踪该方向与买卖点"}
            </button>
          )}
          {error && <p role="alert">{error}</p>}
          <details>
            <summary>查看技术指标与算法依据</summary>
            <ActionablePlanEvidence
              result={{ ...plan, tracks: track ? [track] : plan.tracks }}
            />
          </details>
        </div>
        {plan.priceBasis === "UNIT_NAV" && /^\d{6}$/.test(code) && (
          <FundNavTrendChart fundCode={code} track={track} />
        )}
      </div>
    </article>
  );
}

export default function ValueDirections({
  data,
  onTrack,
  onViewMacro,
}: {
  data?: DailyResearch | null;
  onTrack?: (input: DirectionTrackInput) => Promise<void>;
  onViewMacro?: () => void;
}) {
  const report = data?.report;
  if (!report) return null;
  const directions = report.valueDirections
    ? [...report.valueDirections].sort((a, b) =>
        comparePriority(
          bestDirectionPriority(a.plans),
          bestDirectionPriority(b.plans),
        ),
      )
    : undefined;
  return (
    <section
      key={data?.reportRunId ?? report.asOf ?? "no-report"}
      className="op-value-directions"
      aria-label="今日价值方向与买卖计划"
    >
      <header>
        <h3>
          {data?.state === "READY"
            ? "今日价值方向与买卖计划"
            : "历史价值方向与买卖计划"}
        </h3>
        <small>
          {researchScheduleLabel(data?.schedule)} ·
          交易日14:30独立核对场外基金（北京时间）
        </small>
      </header>
      {report.industryResearchCoverage &&
        report.industryResearchCoverage.omittedCandidateCount > 0 && (
          <div
            className="op-industry-risk"
            role="status"
            aria-label="行业反方核查覆盖缺口"
          >
            <p>
              本轮行业反方核查覆盖
              {report.industryResearchCoverage.candidateCount}个候选，另有
              {report.industryResearchCoverage.omittedCandidateCount}
              个候选未纳入；检索覆盖存在缺口
            </p>
            {(report.industryResearchCoverage.omittedRiskCandidateCount ?? 0) >
              0 && (
              <strong>
                其中含
                {report.industryResearchCoverage.omittedRiskCandidateCount}
                个历史风险赛道。
              </strong>
            )}
          </div>
        )}
      {directions?.length ? (
        directions.map((direction) => (
          <section
            className="op-value-direction"
            aria-label={direction.title}
            key={direction.key}
          >
            <div className="op-value-thesis">
              <header>
                <h3>{readingText(direction.title, 70)}</h3>
                <span
                  className={`op-value-signal signal-${direction.signal.toLowerCase()}`}
                >
                  {labels[direction.signal]}
                </span>
                <IndustryCycleBadge cycle={direction.industryCycle} />
              </header>
              <p>{readingText(direction.thesis, 260)}</p>
              <IndustryCycleWarning
                cycle={direction.industryCycle}
                warning={direction.industryRiskWarning}
                sources={report.sources}
              />
              <DirectionSignals
                rows={direction.catalysts}
                sources={report.sources}
              />
              <DirectionSignals
                rows={direction.risks}
                sources={report.sources}
                risk
              />
              <details>
                <summary>综合依据与后续观察</summary>
                {direction.industryCycle?.evidenceSummary && (
                  <p>
                    产业阶段依据：
                    {readingText(direction.industryCycle.evidenceSummary, 400)}
                  </p>
                )}
                {direction.sourceIds.map((id) => {
                  const source = report.sources.find((item) => item.id === id);
                  return (
                    source && (
                      <p key={id}>
                        {sourceHref(source.url) ? (
                          <a
                            href={sourceHref(source.url)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {sourceTitle(source.title, 100)}
                          </a>
                        ) : (
                          sourceTitle(source.title, 100)
                        )}
                      </p>
                    )
                  );
                })}
                <MacroReferences rows={direction.macroEvidence} />
                {!!direction.macroEvidence?.length && onViewMacro && (
                  <button type="button" onClick={onViewMacro}>
                    查看市场证据
                  </button>
                )}
                {direction.watchFor.map((text) => (
                  <p key={text}>{readingText(text, 180)}</p>
                ))}
              </details>
            </div>
            <div className="op-value-funds">
              {direction.plans.length ? (
                direction.plans.map((plan) => (
                  <FundPlan
                    key={`${plan.subjectKind}:${plan.subjectId}:${plan.selectedTrack ?? plan.track}`}
                    plan={plan}
                    direction={direction}
                    sources={report.sources}
                    reportRunId={data.reportRunId}
                    onTrack={onTrack}
                  />
                ))
              ) : (
                <p>等待真实价格依据，暂不提供到价跟踪。</p>
              )}
            </div>
          </section>
        ))
      ) : directions ? (
        <p>当前证据尚未形成有充分依据的价值方向。</p>
      ) : (
        <>
          <p>
            此份报告未生成综合方向，以下展示研究价格计划；最新买卖区间以价格依据中的计算日期与算法版本为准。
          </p>
          {report.actionablePlans?.map((plan) => (
            <ActionablePlan
              key={`${plan.subjectKind}:${plan.subjectId}:${plan.selectedTrack ?? plan.track}`}
              result={plan}
              sources={report.sources}
            />
          ))}
        </>
      )}
    </section>
  );
}

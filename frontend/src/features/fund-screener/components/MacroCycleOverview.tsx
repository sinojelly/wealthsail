import { useId, useState } from "react";
import {
  Compass,
  Globe2,
  Landmark,
  ChartNoAxesCombined,
  Zap,
} from "lucide-react";
import type { OpportunityContext } from "../../../api/investment";
import type {
  FedRateExpectation,
  MacroCycleInsights,
  MacroPolicySignal,
} from "../../../api/macro-cycle";
import {
  buildCycleOverview,
  cycleFacts,
  cycleReading,
  cycleReportCutoff,
  domesticMacroReading,
  type CycleFact,
  type CycleView,
} from "../macro-cycle-overview";
import { sourceHref, timeLabel, monthLabel } from "../macro-display";
import { readingText, researchSummary, sourceTitle } from "../research-reading";
import ObservationSection from "./ObservationSection";
import MacroCycleTrends from "./MacroCycleTrends";
import "../macro-cycle-overview.css";

type Sources = NonNullable<
  NonNullable<OpportunityContext["dailyResearch"]>["report"]
>["sources"];

function ViewMeta({ view, sources }: { view: CycleView; sources: Sources }) {
  const label =
    view.evidenceKind === "MARKET_EXPECTATION"
      ? "市场预期"
      : view.evidenceKind === "OBSERVED_FACT"
        ? "事实摘录"
        : "研究研判";
  return (
    <small className="op-cycle-meta">
      <span>{view.origin ?? label}</span>
      {view.asOf && <time dateTime={view.asOf}>{timeLabel(view.asOf)}</time>}
      {[...new Set(view.sourceIds ?? [])].map((id) => {
        const source = sources.find((item) => item.id === id);
        const href = sourceHref(source?.url);
        return source && href ? (
          <a key={id} href={href} target="_blank" rel="noreferrer">
            {sourceTitle(source.title, 80)}
          </a>
        ) : null;
      })}
    </small>
  );
}

function CycleRow({
  title,
  icon,
  view,
  missing,
  sources,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  view: CycleView | null;
  missing: string;
  sources: Sources;
  children?: React.ReactNode;
}) {
  return (
    <div className="op-cycle-row" role="group" aria-label={title}>
      <div className="op-cycle-row-label">
        {icon}
        <h4>{title}</h4>
      </div>
      <div className="op-cycle-row-content">
        {view || children ? (
          <>
            {view?.stage && (
              <strong className="op-cycle-stage">
                {readingText(view.stage, Infinity)}
              </strong>
            )}
            {children ??
              (view?.summary && (
                <p>{readingText(cycleReading(view.summary), Infinity)}</p>
              ))}
            {!children && view?.impact && (
              <p className="op-cycle-impact">
                {readingText(view.impact, Infinity)}
              </p>
            )}
            {!children && view?.action && (
              <p className="op-cycle-action">
                {readingText(view.action, Infinity)}
              </p>
            )}
            {view && <ViewMeta view={view} sources={sources} />}
          </>
        ) : (
          <p className="op-cycle-missing">{missing}</p>
        )}
      </div>
    </div>
  );
}

function ChainStep({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <li>
      <h5>{title}</h5>
      {children}
    </li>
  );
}

function StepFacts({
  facts,
  codes,
}: {
  facts: CycleFact[];
  codes: readonly string[];
}) {
  const visible = codes.flatMap((code) =>
    facts.filter((fact) => fact.code === code),
  );
  if (!visible.length) return null;
  return (
    <div className="op-cycle-metric-chips">
      {visible.map((fact) => {
        const href = sourceHref(fact.sourceUri);
        const label = (
          <>
            {fact.label}{" "}
            <strong>
              {fact.value}
              {fact.unit}
            </strong>
          </>
        );
        return (
          <span key={fact.code}>
            {href ? (
              <a href={href} target="_blank" rel="noreferrer">
                {label}
              </a>
            ) : (
              label
            )}
            {(fact.asOf || fact.referencePeriod) && (
              <small>
                {fact.referencePeriod
                  ? monthLabel(fact.referencePeriod)
                  : fact.asOf?.slice(0, 10)}
              </small>
            )}
          </span>
        );
      })}
    </div>
  );
}

const signed = (value: number) => `${value > 0 ? "+" : ""}${value}`;

function FedPricing({
  meetings,
  facts,
}: {
  meetings: FedRateExpectation[];
  facts: CycleFact[];
}) {
  const nominal = facts.find((row) => row.code === "US_T10_NOMINAL");
  const real = facts.find((row) => row.code === "US_T10_REAL");
  const highRates = Number(nominal?.value) >= 4.5 || Number(real?.value) >= 2;
  const first = meetings.find((meeting) => !meeting.cumulative) ?? meetings[0];
  const moves =
    first && first.complete
      ? [
          { label: "加息", probability: first.hikeProbability },
          { label: "降息", probability: first.cutProbability },
          { label: "维持利率", probability: first.holdProbability },
        ].sort((a, b) => b.probability - a.probability)
      : [];
  const leading = moves[0];
  const clear = leading && leading.probability !== moves[1]?.probability;
  const pricing = clear
    ? `${monthLabel(first.meetingMonth)}${first.cumulative ? "累计" : ""}${leading.label}定价 ${leading.probability}%`
    : first?.complete
      ? "市场对利率方向的定价较为分散"
      : "利率期货概率待补齐";
  return (
    <section
      className="op-cycle-fed-pricing"
      aria-label="美联储利率预期与流动性判断"
    >
      <h4>🌍 美联储利率预期与流动性判断</h4>
      <p
        className="op-cycle-rate-call"
        data-tone={highRates ? "tight" : undefined}
      >
        <strong>
          {pricing}
          {highRates && " · 全球资金成本偏高"}
        </strong>
      </p>
      {!meetings.length && <p>当前报告时点没有可核对的 CME 会议档位。</p>}
      {meetings.map((meeting) => {
        const href = sourceHref(meeting.sourceUri);
        const month = monthLabel(meeting.meetingMonth);
        const label = meeting.cumulative ? `${month}累计` : month;
        return (
          <p key={`${meeting.meetingMonth}:${meeting.cumulative}`}>
            <strong>CME 利率期货 · {label}</strong>{" "}
            {meeting.complete
              ? `加息总概率 ${meeting.hikeProbability}%，降息 ${meeting.cutProbability}%，维持不变 ${meeting.holdProbability}%`
              : "已报道档位（本组概率未收齐）"}{" "}
            (
            {meeting.outcomes.map(({ changeBp, probability }, index) => {
              const text = `${changeBp === 0 ? "不变" : `${signed(changeBp)}bp`} ${probability}%`;
              return (
                <span key={changeBp}>
                  {index > 0 && " | "}
                  {changeBp === meeting.mainOutcome?.changeBp ? (
                    <strong title="主定价档位">{text}</strong>
                  ) : (
                    text
                  )}
                </span>
              );
            })}
            {meeting.expectedChangeBp != null &&
              ` · 期望 ${signed(meeting.expectedChangeBp)}bp`}
            ){meeting.asOf && <small> · {timeLabel(meeting.asOf)}</small>}
            {href && (
              <>
                {" "}
                ·{" "}
                <a href={href} target="_blank" rel="noreferrer">
                  来源
                </a>
              </>
            )}
          </p>
        );
      })}
      {(nominal || real) && (
        <p>
          {[
            nominal && `美国 10 年名义利率 ${nominal.value}%`,
            real && `实际利率 ${real.value}%`,
          ]
            .filter(Boolean)
            .join("，")}
          。
        </p>
      )}
      <p className="op-cycle-rate-impact">
        <strong>资产影响 </strong>
        {highRates
          ? "高实际利率会增加持有黄金的机会成本，也可能压低高估值科技资产的估值。配置时以价格区间和持仓风险为参考。"
          : "利率与实际收益率变化会影响黄金的持有成本和成长公司的估值，可结合相应价格区间一同观察。"}
      </p>
    </section>
  );
}

function AuditNotes({
  notes,
  original,
}: {
  notes: string[];
  original?: string | null;
}) {
  if (!notes.length && !original) return null;
  return (
    <details className="op-cycle-audit-notes">
      <summary>ⓘ 查看原始审计文本与数据口径说明</summary>
      {original && <p className="op-cycle-original">{original}</p>}
      {!!notes.length && (
        <ul>
          {notes.map((note) => (
            <li key={note}>{readingText(note, Infinity)}</li>
          ))}
        </ul>
      )}
    </details>
  );
}

function RateReview({
  expectation,
}: {
  expectation: NonNullable<MacroCycleInsights["rateExpectation"]>;
}) {
  const details = [
    ["2 年期−有效利率", expectation.spreadBp],
    ["2 年期近 5 个观测", expectation.us2yChange5Bp],
    ["10 年期近 5 个观测", expectation.us10yChange5Bp],
  ]
    .map(([label, value]) =>
      typeof value === "number" && Number.isFinite(value)
        ? `${label} ${signed(value)} bp`
        : null,
    )
    .filter(Boolean);
  return (
    <p>
      {expectation.state === "TIGHTER"
        ? "短端利率定价偏紧。"
        : expectation.state === "EASIER"
          ? "短端利率定价趋于宽松。"
          : ""}
      {details.length > 0 && `${details.join("，")}。`}
      <small>
        利差反映市场定价，含期限溢价。
        {expectation.asOf && timeLabel(expectation.asOf)}
      </small>
    </p>
  );
}

export default function MacroCycleOverview({
  data,
  historical = false,
  factView = false,
  indicatorWall = false,
}: {
  data: OpportunityContext;
  historical?: boolean;
  factView?: boolean;
  indicatorWall?: boolean;
}) {
  const report = data.dailyResearch?.report;
  const reportKey =
    data.dailyResearch?.reportRunId ?? report?.asOf ?? "no-report";
  const [expandedReport, setExpandedReport] = useState<string | null>(null);
  const expanded = expandedReport === reportKey;
  const summaryId = useId();
  const model = buildCycleOverview(data);
  const sources = report?.sources ?? [];
  const full = researchSummary(
    cycleReading(model.market?.summary ?? ""),
    Infinity,
  );
  const preview = researchSummary(full, 280);
  const cycle = report?.cycleInsights;
  const snapshot = cycle?.macroSnapshot;
  const retained =
    historical ||
    !!snapshot ||
    (data.dailyResearch?.state !== "READY" && !!report);
  const cutoff = retained ? cycleReportCutoff(data) : null;
  const observed = snapshot
    ? { ...data, ...snapshot, alphaPublic: null }
    : data;
  const facts = cycleFacts(observed, cutoff);
  const marketFacts = historical
    ? cycleFacts(observed, cutoff, true)
    : cycleFacts(data, undefined, true);
  const signals = cycle?.policySignals ?? [];
  const inflationSignals = signals
    .filter((row) => row.kind === "INFLATION_DRIVER")
    .slice(0, 3);
  const expectation = cycle?.rateExpectation;
  const meetings = cycle?.fedRateExpectations ?? [];
  const rateCandidates = signals.filter(
    (row) =>
      row.kind !== "INFLATION_DRIVER" &&
      (!meetings.length || row.kind !== "MARKET_RATE_ODDS"),
  );
  const rateSignals = [
    ...new Set([
      rateCandidates.find((row) => row.kind === "CENTRAL_BANK_REMARK"),
      rateCandidates.find((row) => row.kind === "INSTITUTION_FORECAST"),
      ...rateCandidates,
    ]),
  ]
    .filter((row) => row !== undefined)
    .slice(0, 3);
  const inflationCodes = [
    "US_CPI_YOY",
    "US_CPI_MOM",
    "US_CORE_CPI_YOY",
    "US_PPI_YOY",
  ];
  if (!indicatorWall) inflationCodes.push("US_BRENT");
  const notes = cycle?.auditNotes ?? [];
  const moneySpread = historical
    ? snapshot?.moneySupplySpread
    : (data.marketCycleSnapshot?.moneySupplySpread ??
      snapshot?.moneySupplySpread);
  const original = report?.macroCycle || report?.summary;
  const brief = domesticMacroReading(marketFacts, moneySpread);
  // Current charts and the dated report judgment are displayed as separate evidence.
  // A historical preview is the only view that freezes both to the same clock.
  const trendData = historical ? observed : data;
  const trendCutoff = historical ? cutoff : null;
  const rateState = !retained || historical ? expectation?.state : undefined;
  const haveGlobal = !!(
    model.global ||
    signals.length ||
    meetings.length ||
    expectation ||
    facts.some((fact) => fact.code.startsWith("US_"))
  );
  const cn10 = facts.find((row) => row.code === "CN_T10");
  const us10 = facts.find((row) => row.code === "US_T10_NOMINAL");
  const spread =
    cn10?.asOf &&
    us10?.asOf &&
    cn10.asOf.slice(0, 10) === us10.asOf.slice(0, 10)
      ? Math.round((Number(cn10.value) - Number(us10.value)) * 100)
      : null;
  if (!factView) {
    return (
      <ObservationSection
        className="op-macro-overview op-cycle-overview"
        label="今日宏观总纲"
        title="宏观周期与今日总纲"
      >
        <div className="op-cycle-brief">
          <section aria-label="今日宏观定调">
            <h4>📌 今日宏观定调</h4>
            <dl className="op-cycle-brief-cards">
              {brief.map((row) => (
                <div key={row.title}>
                  <dt>{row.title}</dt>
                  <dd>{row.text}</dd>
                </div>
              ))}
            </dl>
          </section>
          <FedPricing meetings={meetings} facts={marketFacts} />
        </div>
        <div className="op-cycle-chart-context">
          {historical
            ? "研究时点的市场数据"
            : "本地最新市场数据 · 各指标日期见图例"}
        </div>
        <MacroCycleTrends
          data={trendData}
          cutoff={trendCutoff}
          facts={marketFacts}
          rateState={rateState}
          composite={
            historical
              ? snapshot?.composite
              : (data.marketCycleSnapshot?.composite ?? snapshot?.composite)
          }
        />
        <AuditNotes key={reportKey} notes={notes} original={original} />
      </ObservationSection>
    );
  }
  return (
    <ObservationSection
      className="op-macro-overview op-cycle-overview"
      label="今日宏观总纲"
      title={
        factView
          ? historical
            ? "历史宏观事实"
            : "当前宏观事实"
          : "宏观周期与今日总纲"
      }
    >
      <div className="op-cycle-caption">
        <Compass size={17} aria-hidden="true" />
        {report && (factView || data.dailyResearch?.state !== "READY") && (
          <h4>
            {data.dailyResearch?.state === "READY"
              ? "宏观周期与今日总纲"
              : "历史宏观研判"}
          </h4>
        )}
        {report && (
          <small>
            研判证据：{timeLabel(report.asOf ?? data.dailyResearch?.asOf)}
          </small>
        )}
      </div>
      <div className="op-cycle-rows">
        <CycleRow
          title="全球利率与流动性"
          icon={<Globe2 size={18} aria-hidden="true" />}
          view={model.global}
          missing="本报告未单列全球利率周期结论。"
          sources={sources}
        >
          {haveGlobal && (
            <ol className="op-cycle-chain" aria-label="全球利率与流动性推导链">
              {!!meetings.length && (
                <ChainStep title="联储会议期货定价">
                  <FedPricing meetings={meetings} facts={facts} />
                </ChainStep>
              )}
              <ChainStep title="通胀与需求源头">
                <StepFacts facts={facts} codes={inflationCodes} />
                {!facts.some((fact) => inflationCodes.includes(fact.code)) && (
                  <p>该期缺少可核对的通胀读数。</p>
                )}
                {inflationSignals.map((row) => (
                  <PolicySignal key={`${row.title}:${row.text}`} row={row} />
                ))}
              </ChainStep>
              <ChainStep
                title={`联储表态与机构预测${rateSignals.length ? ` · ${rateSignals.length} 条` : ""}`}
              >
                {rateSignals.map((row) => (
                  <PolicySignal key={`${row.title}:${row.text}`} row={row} />
                ))}
                {!rateSignals.length && (
                  <p>该期未找到可归属的官员表态或机构预测。</p>
                )}
              </ChainStep>
              <ChainStep title="核心流动性水位">
                <StepFacts
                  facts={facts}
                  codes={[
                    "US_FED_FUNDS",
                    "US_T2_NOMINAL",
                    "US_T10_NOMINAL",
                    "US_T10_REAL",
                    "CN_T10",
                  ]}
                />
                {spread !== null && (
                  <p>
                    同日中美 10 年利差 {spread > 0 ? "+" : ""}
                    {spread} bp。
                  </p>
                )}
                {expectation && <RateReview expectation={expectation} />}
                {!expectation && !us10 && <p>该期缺少可核对的利率读数。</p>}
              </ChainStep>
              <ChainStep title="市场定价传导">
                {cycleReading(
                  model.global?.impact ?? model.global?.summary ?? "",
                ) && (
                  <p>
                    {readingText(
                      cycleReading(
                        model.global?.impact ?? model.global?.summary ?? "",
                      ),
                      Infinity,
                    )}
                  </p>
                )}
                <p>
                  实际利率上行可能抬高长久期资产折现率和黄金持有成本。盈利、风险溢价和避险需求会影响实际表现。
                </p>
                {model.global?.action && (
                  <p className="op-cycle-action">
                    {readingText(cycleReading(model.global.action), Infinity)}
                  </p>
                )}
              </ChainStep>
            </ol>
          )}
        </CycleRow>
        <CycleRow
          title="国内货币、财政与信用"
          icon={<Landmark size={18} aria-hidden="true" />}
          view={model.domestic}
          missing="国内周期以本报告总纲和下列已采集数据为准。"
          sources={sources}
        >
          <ol className="op-cycle-chain" aria-label="国内经济与货币信用推导链">
            <ChainStep title="景气与价格信号">
              {model.domestic?.summary && (
                <p>
                  {readingText(cycleReading(model.domestic.summary), Infinity)}
                </p>
              )}
            </ChainStep>
            <ChainStep title="货币与信用传导">
              {moneySpread?.summary && <p>{moneySpread.summary}</p>}
              <p>
                {readingText(
                  cycleReading(model.domestic?.impact ?? ""),
                  Infinity,
                ) || "关注货币与信用改善能否传导到支出、订单和盈利。"}
              </p>
            </ChainStep>
          </ol>
        </CycleRow>
        <CycleRow
          title="市场阶段与操作总纲"
          icon={<ChartNoAxesCombined size={18} aria-hidden="true" />}
          view={model.market}
          missing="市场阶段尚未单列。"
          sources={sources}
        >
          <p id={summaryId}>{expanded ? full : preview}</p>
          {full !== preview && (
            <button
              type="button"
              className="op-macro-expand"
              aria-expanded={expanded}
              aria-controls={summaryId}
              onClick={() => setExpandedReport(expanded ? null : reportKey)}
            >
              {expanded ? "收起今日总纲" : "展开今日总纲"}
            </button>
          )}
          {model.market?.action && (
            <p className="op-cycle-action">
              {readingText(cycleReading(model.market.action), Infinity)}
            </p>
          )}
        </CycleRow>
        {!!model.factors.length && (
          <details
            className="op-cycle-factors-details"
            role="group"
            aria-label="今日关键影响因素"
          >
            <summary>
              <Zap size={18} aria-hidden="true" />
              今日关键影响因素 · {model.factors.length} 项
            </summary>
            <ol className="op-cycle-factors">
              {model.factors.map((view, index) => (
                <li key={`${view.title}:${index}`}>
                  <strong>
                    {readingText(
                      view.title || view.summary || "关键因素",
                      Infinity,
                    )}
                  </strong>
                  {view.title && view.summary && (
                    <span>
                      {readingText(cycleReading(view.summary), Infinity)}
                    </span>
                  )}
                  {view.impact && (
                    <span>{readingText(view.impact, Infinity)}</span>
                  )}
                  <ViewMeta view={view} sources={sources} />
                </li>
              ))}
            </ol>
          </details>
        )}
      </div>
      {!indicatorWall && !!marketFacts.length && (
        <div
          className="op-core-facts op-cycle-facts"
          aria-label="已采集宏观数据"
        >
          {marketFacts.map((fact) => (
            <div key={fact.code}>
              <span>{fact.label}</span>
              <strong>
                <span>{fact.value}</span> {fact.unit}
              </strong>
              <small>
                {fact.referencePeriod
                  ? monthLabel(fact.referencePeriod)
                  : timeLabel(fact.asOf)}
              </small>
            </div>
          ))}
        </div>
      )}
      {!!model.industries.length && (
        <details className="op-cycle-industries op-print-disclosure">
          <summary>核心行业周期与趋势 · {model.industries.length} 项</summary>
          <div className="op-cycle-industry-scroll">
            <table aria-label="核心行业周期与趋势">
              <thead>
                <tr>
                  <th>方向</th>
                  <th>所处阶段</th>
                  <th>核心驱动与观察</th>
                </tr>
              </thead>
              <tbody>
                {model.industries.map((view, index) => (
                  <tr key={`${view.industryId ?? view.label}:${index}`}>
                    <th scope="row">
                      {readingText(
                        view.label || view.industryId || "行业方向",
                        Infinity,
                      )}
                    </th>
                    <td>
                      <span className="op-cycle-stage">
                        {readingText(view.stage || "观察中", Infinity)}
                      </span>
                    </td>
                    <td>
                      {readingText(
                        cycleReading(
                          view.driver || view.summary || "详见本报告方向研究。",
                        ),
                        Infinity,
                      )}
                      {view.action && (
                        <p>{readingText(view.action, Infinity)}</p>
                      )}
                      {!!view.fundPositions?.length && (
                        <ul
                          className="op-cycle-fund-positions"
                          aria-label="当前基金位置"
                        >
                          {view.fundPositions.map((fund) => (
                            <li key={fund.fundCode}>
                              <strong>{fund.label}</strong> {fund.fundCode} ·{" "}
                              {fund.status}
                              {fund.asOf && (
                                <small> · 净值日 {timeLabel(fund.asOf)}</small>
                              )}
                            </li>
                          ))}
                        </ul>
                      )}
                      <ViewMeta view={view} sources={sources} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      <AuditNotes key={reportKey} notes={notes} />
    </ObservationSection>
  );
}

function PolicySignal({ row }: { row: MacroPolicySignal }) {
  const href = sourceHref(row.sourceUri);
  return (
    <p className="op-cycle-policy-signal">
      {readingText(row.text, Infinity)}{" "}
      <small>
        {row.asOf && <time dateTime={row.asOf}>{timeLabel(row.asOf)}</time>}{" "}
        {href && (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            title={sourceTitle(row.title, 100)}
          >
            来源原文
          </a>
        )}
      </small>
    </p>
  );
}

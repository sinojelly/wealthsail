import { useId, useState } from "react";
import {
  Compass,
  Globe2,
  Landmark,
  ChartNoAxesCombined,
  Zap,
} from "lucide-react";
import type { OpportunityContext } from "../../../api/investment";
import type { MacroPolicySignal } from "../../../api/macro-cycle";
import {
  buildCycleOverview,
  cycleFacts,
  cycleReading,
  cycleReportCutoff,
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

export default function MacroCycleOverview({
  data,
  historical = false,
  factView = false,
}: {
  data: OpportunityContext;
  historical?: boolean;
  factView?: boolean;
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
  const snapshot = report?.cycleInsights?.macroSnapshot;
  const retained =
    historical ||
    !!snapshot ||
    (data.dailyResearch?.state !== "READY" && !!report);
  const cutoff = retained ? cycleReportCutoff(data) : null;
  const observed = snapshot
    ? { ...data, ...snapshot, alphaPublic: null }
    : data;
  const facts = cycleFacts(observed, cutoff);
  const factGrid = factView ? (historical ? facts : cycleFacts(data)) : [];
  const chainFacts = factView ? [] : facts;
  const signals = report?.cycleInsights?.policySignals ?? [];
  const expectation = report?.cycleInsights?.rateExpectation;
  const haveGlobal = !!(
    model.global ||
    signals.length ||
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
  const change5 = expectation?.us10yChange5Bp;
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
              <ChainStep title="通胀与需求源头">
                <StepFacts
                  facts={chainFacts}
                  codes={[
                    "US_CPI_YOY",
                    "US_CPI_MOM",
                    "US_CORE_CPI_YOY",
                    "US_PPI_YOY",
                    "US_BRENT",
                  ]}
                />
                {!facts.some((fact) =>
                  /^US_(CPI|CORE_CPI|PPI|BRENT)/.test(fact.code),
                ) && <p>该期缺少可核对的通胀或油价读数。</p>}
                {signals
                  .filter((row) => row.kind === "INFLATION_DRIVER")
                  .map((row) => (
                    <PolicySignal key={`${row.title}:${row.text}`} row={row} />
                  ))}
              </ChainStep>
              <ChainStep title="联储表态与机构预测">
                {signals
                  .filter((row) => row.kind !== "INFLATION_DRIVER")
                  .map((row) => (
                    <PolicySignal key={`${row.title}:${row.text}`} row={row} />
                  ))}
                {!signals.some((row) => row.kind !== "INFLATION_DRIVER") && (
                  <p>该期未找到可归属的官员表态或机构预测。</p>
                )}
              </ChainStep>
              <ChainStep title="核心流动性水位">
                <StepFacts
                  facts={chainFacts}
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
                {change5 != null && (
                  <p>
                    美国 10 年名义收益率近五个交易日 {change5 > 0 ? "+" : ""}
                    {change5} bp。
                  </p>
                )}
                {expectation && (
                  <p>
                    {readingText(expectation.summary, Infinity)}{" "}
                    <small>
                      利差反映市场定价，含期限溢价。
                      {expectation.asOf && timeLabel(expectation.asOf)}
                    </small>
                  </p>
                )}
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
              <StepFacts facts={chainFacts} codes={["PMI", "CPI_YOY", "PPI_YOY"]} />
              {model.domestic?.summary && (
                <p>
                  {readingText(cycleReading(model.domestic.summary), Infinity)}
                </p>
              )}
            </ChainStep>
            <ChainStep title="货币与信用传导">
              <StepFacts
                facts={chainFacts}
                codes={["M1_YOY", "M2_YOY", "TSF_STOCK_YOY", "M1_M2_GAP"]}
              />
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
      {!factView && (
        <MacroCycleTrends
          data={observed}
          supplemental={snapshot ? data : undefined}
          cutoff={cutoff}
          facts={facts}
          changeBp={change5}
        />
      )}
      {!!factGrid.length && (
        <div
          className="op-core-facts op-cycle-facts"
          aria-label="已采集宏观数据"
        >
          {factGrid.map((fact) => (
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
                      <ViewMeta view={view} sources={sources} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
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
        {href ? (
          <a href={href} target="_blank" rel="noreferrer">
            {sourceTitle(row.title, 46)}
          </a>
        ) : (
          sourceTitle(row.title, 46)
        )}
      </small>
    </p>
  );
}

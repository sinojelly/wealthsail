import { useId, useState } from "react";
import {
  Compass,
  Globe2,
  Landmark,
  ChartNoAxesCombined,
  Zap,
} from "lucide-react";
import type { OpportunityContext } from "../../../api/investment";
import {
  buildCycleOverview,
  cycleFacts,
  type CycleView,
} from "../macro-cycle-overview";
import { sourceHref, timeLabel, monthLabel } from "../macro-display";
import { readingText, researchSummary, sourceTitle } from "../research-reading";
import ObservationSection from "./ObservationSection";
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
        {view ? (
          <>
            {view.stage && (
              <strong className="op-cycle-stage">
                {readingText(view.stage, Infinity)}
              </strong>
            )}
            {children ??
              (view.summary && <p>{readingText(view.summary, Infinity)}</p>)}
            {view.impact && (
              <p className="op-cycle-impact">
                {readingText(view.impact, Infinity)}
              </p>
            )}
            {view.action && (
              <p className="op-cycle-action">
                {readingText(view.action, Infinity)}
              </p>
            )}
            <ViewMeta view={view} sources={sources} />
          </>
        ) : (
          <p className="op-cycle-missing">{missing}</p>
        )}
      </div>
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
  const full = researchSummary(model.market?.summary ?? "", Infinity);
  const preview = researchSummary(full, 280);
  const facts = cycleFacts(data);
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
        />
        <CycleRow
          title="国内货币、财政与信用"
          icon={<Landmark size={18} aria-hidden="true" />}
          view={model.domestic}
          missing="国内周期以本报告总纲和下列已采集数据为准。"
          sources={sources}
        />
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
        </CycleRow>
        {!!model.factors.length && (
          <div
            className="op-cycle-row"
            role="group"
            aria-label="今日关键影响因素"
          >
            <div className="op-cycle-row-label">
              <Zap size={18} aria-hidden="true" />
              <h4>今日关键影响因素</h4>
            </div>
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
                    <span>{readingText(view.summary, Infinity)}</span>
                  )}
                  {view.impact && (
                    <span>{readingText(view.impact, Infinity)}</span>
                  )}
                  <ViewMeta view={view} sources={sources} />
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
      {!!facts.length && (
        <div
          className="op-core-facts op-cycle-facts"
          aria-label="已采集宏观数据"
        >
          {facts.map((fact) => (
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
                        {readingText(view.stage || "阶段未单列", Infinity)}
                      </span>
                    </td>
                    <td>
                      {readingText(
                        view.driver || view.summary || "详见本报告方向研究。",
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

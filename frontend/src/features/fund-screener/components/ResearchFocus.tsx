import type { OpportunityContext, OverviewItem } from "../../../api/investment";
import { timeLabel, monthLabel } from "../macro-display";
import { readingText } from "../research-reading";

const resultLabels: Record<string, string> = {
  CONFIRMED_RISK: "已确认个人风险",
  LIMITED_CLUE: "有限研究线索",
  STRATEGY_CANDIDATE: "已满足策略条件",
  INCONCLUSIVE: "继续观察",
};

export default function ResearchFocus({
  data,
  onResearchIndex,
}: {
  data: OpportunityContext;
  onResearchIndex: (id: string) => void;
}) {
  const seen = new Set<string>();
  const observations = (data.researchOverview?.highlights ?? []).filter(
    (row) => {
      if (
        ["EVENT", "UPCOMING", "PERSONAL_RISK"].includes(row.kind) ||
        seen.has(row.id)
      )
        return false;
      seen.add(row.id);
      return true;
    },
  );
  const merged: Array<{ item: OverviewItem; related: OverviewItem[] }> = [];
  for (const item of observations) {
    const parent =
      item.indexId && item.kind.startsWith("INDEX_")
        ? merged.find(
            (row) =>
              row.item.indexId === item.indexId &&
              row.item.kind.startsWith("INDEX_"),
          )
        : null;
    if (parent) parent.related.push(item);
    else merged.push({ item, related: [] });
  }
  const qualified = observations.some(
    (row) =>
      row.resultType === "STRATEGY_CANDIDATE" || row.kind === "QUALIFICATION",
  );
  const reportPlans = new Set(
    data.dailyResearch?.report?.upcoming.map((row) => row.id) ?? [],
  );
  const calendar = (data.researchOverview?.highlights ?? [])
    .filter((row) => row.kind === "UPCOMING" && !reportPlans.has(row.id))
    .slice(0, 3);
  return (
    <section className="op-focus" aria-label="当前资产观察">
      <header>
        <h3>当前资产观察</h3>
        <p>
          {qualified
            ? "存在通过规定条件的策略研究候选，仍需人工复核。"
            : "尚无符合完整条件的基金候选"}
        </p>
      </header>
      {merged.length === 0 && (
        <p>当前证据不足以形成可靠判断；可在下方工作台核查具体标的。</p>
      )}
      <div className="op-focus-list">
        {merged.slice(0, 4).map(({ item, related }) => {
          const index = data.marketEvidence?.find(
            (row) => row.indexId === item.indexId,
          );
          const failed = item.condition === "FAILED";
          const unknown = item.condition === "UNKNOWN" || item.tone === "GRAY";
          const tone = unknown
            ? "op-tone-gray"
            : failed
              ? "op-tone-orange"
              : item.resultType === "STRATEGY_CANDIDATE"
                ? "op-tone-green"
                : "op-tone-blue";
          return (
            <article className="op-focus-item" key={item.id}>
              <details>
                <summary>
                  <span className={tone}>{item.title}</span>
                  <small>
                    {unknown
                      ? "无法核验"
                      : failed
                        ? "市场趋势逆风"
                        : (resultLabels[item.resultType ?? ""] ??
                          "有限事实观察")}
                  </small>
                </summary>
                <div className="op-focus-evidence">
                  <p>{readingText(item.summary, 400)}</p>
                  {item.impact && <p>{readingText(item.impact, 240)}</p>}
                  {index?.trend?.state === "READY" && (
                    <dl className="op-focus-metrics">
                      <div>
                        <dt>相对MA200</dt>
                        <dd>
                          {typeof index.trend.distanceMA200 === "number" &&
                          Number.isFinite(index.trend.distanceMA200)
                            ? `${(index.trend.distanceMA200 * 100).toFixed(2)}%`
                            : "—"}
                        </dd>
                      </div>
                      <div>
                        <dt>63期动量</dt>
                        <dd>
                          {typeof index.trend.momentum63 === "number" &&
                          Number.isFinite(index.trend.momentum63)
                            ? `${(index.trend.momentum63 * 100).toFixed(2)}%`
                            : "—"}
                        </dd>
                      </div>
                    </dl>
                  )}
                  {related.map((row) => (
                    <p key={row.id}>{readingText(row.summary, 240)}</p>
                  ))}
                  {!!item.counterConditions?.length && (
                    <div>
                      <strong>反方与限制</strong>
                      {item.counterConditions.map((text) => (
                        <p key={text}>{readingText(text, 200)}</p>
                      ))}
                    </div>
                  )}
                  <small>
                    依据期：
                    {item.asOf
                      ? /^\d{4}-\d{2}$/.test(item.asOf)
                        ? monthLabel(item.asOf)
                        : timeLabel(item.asOf)
                      : "尚未提供"}
                  </small>
                </div>
              </details>
              <div className="op-focus-next">
                <strong>下一步验证</strong>
                {(item.nextValidation?.length
                  ? item.nextValidation.slice(0, 2)
                  : ["核查相关估值、盈利、趋势及个人条件，证据不足时继续观察。"]
                ).map((text) => (
                  <p key={text}>{readingText(text, 160)}</p>
                ))}
              </div>
              {item.indexId && (
                <button
                  type="button"
                  className="op-focus-workbench"
                  onClick={() => onResearchIndex(item.indexId!)}
                >
                  研究
                  {index?.label ??
                    (item.indexId === "hs300"
                      ? "沪深300"
                      : item.indexId === "a500"
                        ? "中证A500"
                        : item.indexId === "zz500"
                          ? "中证500"
                          : "相关指数")}{" "}
                  →
                </button>
              )}
            </article>
          );
        })}
      </div>
      <p className="op-focus-footnote">
        指数条件只解释相应策略；不满足趋势条件不等于预测下跌，独立PE也不证明便宜。
      </p>
      {!!calendar.length && (
        <section className="op-daily-calendar" aria-label="官方发布日历">
          <details>
            <summary>接下来关注 · 官方发布日历</summary>
            {calendar.map((row) => (
              <p key={row.id}>
                {row.title} · {timeLabel(row.asOf)} · 计划尚未发生
              </p>
            ))}
          </details>
        </section>
      )}
    </section>
  );
}

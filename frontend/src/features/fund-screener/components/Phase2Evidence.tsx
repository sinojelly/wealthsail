import type {
  AlphaObservation,
  OpportunityContext,
} from "../../../api/investment";
import { sourceHref, timeLabel, metricValue } from "../macro-display";

const numeric = (raw: unknown): number | null =>
  ((typeof raw === "string" && raw.trim() !== "") || typeof raw === "number") &&
  Number.isFinite(Number(raw))
    ? Number(raw)
    : null;
const observations = [
  ["CN_T10", "中国10年国债收益率", "%"],
  ["M1_YOY", "M1同比", "%"],
  ["M1_M2_GAP", "M1与M2剪刀差", "个百分点"],
  ["SHIBOR_7D", "Shibor一周", "%"],
  ...(
    [
      ["HAIKOU", "海口"],
      ["SANYA", "三亚"],
      ["SHANGHAI", "上海"],
      ["SHENZHEN", "深圳"],
    ] as const
  ).flatMap(([code, city]) => [
    [`HOUSE_${code}_NEW_MOM`, `${city}新房环比`, "%"],
    [`HOUSE_${code}_SECOND_MOM`, `${city}二手房环比`, "%"],
  ]),
];
function Observation({
  row,
  label,
  unit,
}: {
  row?: AlphaObservation;
  label: string;
  unit: string;
}) {
  const value = numeric(row?.value);
  return (
    <article className="op-compact-metric" aria-label={label}>
      <strong>{label}</strong>
      <p>{value === null ? "暂无有效观察" : `${metricValue(value)} ${unit}`}</p>
      <small>
        观察期：{timeLabel(row?.referenceDate ?? row?.asOf)} · 获取：
        {timeLabel(row?.retrievedAt)}
      </small>
      {label.includes("房环比") && (
        <small>价格指数环比变化；不是住宅成交单价。</small>
      )}
      {sourceHref(row?.sourceUri) && (
        <a href={sourceHref(row?.sourceUri)} target="_blank" rel="noreferrer">
          {label}来源
        </a>
      )}
    </article>
  );
}
function Source({ uri, label }: { uri?: string | null; label: string }) {
  return sourceHref(uri) ? (
    <a href={sourceHref(uri)} target="_blank" rel="noreferrer">
      {label}来源
    </a>
  ) : (
    <span>来源尚未提供</span>
  );
}

export default function Phase2Evidence({ data }: { data: OpportunityContext }) {
  const publicData = data.alphaPublic;
  if (!publicData) return null;
  const latest = new Map<string, AlphaObservation>();
  for (const row of publicData.observations ?? []) {
    const previous = latest.get(row.code);
    if (
      !previous ||
      String(row.referenceDate ?? row.asOf ?? "") >
        String(previous.referenceDate ?? previous.asOf ?? "")
    )
      latest.set(row.code, row);
  }
  const pending = observations.filter(
    ([code]) => !data.series?.some((row) => row.code === code),
  );
  const available = pending.filter(
    ([code]) => numeric(latest.get(code)?.value) !== null,
  );
  const missing = pending.filter(
    ([code]) => numeric(latest.get(code)?.value) === null,
  );
  return (
    <section className="op-phase2-evidence" aria-label="公共采集补充证据">
      {!!pending.length && (
        <section aria-label="新增宏观与地产观察">
          <h3>宏观与四城地产观察</h3>
          <div className="op-compact-metrics">
            {available.map(([code, label, unit]) => (
              <Observation
                key={code}
                row={latest.get(code)}
                label={label}
                unit={unit}
              />
            ))}
          </div>
          {!!missing.length && (
            <details>
              <summary>待补充的宏观与地产观察 · {missing.length}项</summary>
              <ul>
                {missing.map(([code, label]) => (
                  <li key={code}>{label}：暂无有效观察</li>
                ))}
              </ul>
            </details>
          )}
        </section>
      )}
      <details>
        <summary>媒体线索、隐含赔率与发射计划</summary>
        <section aria-label="公共事件与计划线索">
          <h4>媒体线索</h4>
          {(publicData.catalysts ?? []).slice(0, 12).map((row) => (
            <article key={row.id}>
              <strong>{row.title}</strong>
              <p>{row.content}</p>
              <p>
                媒体报道，事实待核实 · 公布：{timeLabel(row.publishedAt)} ·
                获取：{timeLabel(row.retrievedAt)}
              </p>
              <Source uri={row.sourceUri} label={row.title} />
            </article>
          ))}
          {!publicData.catalysts?.length && <p>暂无已采集媒体线索。</p>}
          <h4>市场隐含赔率</h4>
          <p>市场隐含赔率，不是统计真实概率。</p>
          {(publicData.odds ?? []).slice(0, 20).map((row) => (
            <article key={`${row.marketId}:${row.outcome}`}>
              <strong>
                {row.question} · {row.outcome}
              </strong>
              <p>
                {numeric(row.impliedProbabilityPct) === null
                  ? "暂无有效赔率"
                  : `${metricValue(numeric(row.impliedProbabilityPct))} %`}
              </p>
              <p>观察：{timeLabel(row.observedAt ?? row.retrievedAt)}</p>
              <Source uri={row.sourceUri} label={row.question} />
            </article>
          ))}
          {!publicData.odds?.length && <p>暂无已采集隐含赔率。</p>}
          <h4>发射计划</h4>
          {(publicData.launches ?? []).slice(0, 12).map((row) => (
            <article key={row.id}>
              <strong>{row.name}</strong>
              <p>
                计划中，尚未发射 · 计划时间：{timeLabel(row.scheduledAt)} ·
                获取：{timeLabel(row.retrievedAt)}
              </p>
              <Source uri={row.sourceUri} label={row.name} />
            </article>
          ))}
          {!publicData.launches?.length && <p>暂无已采集发射计划。</p>}
        </section>
      </details>
      {!!Object.keys(publicData.sourceResults ?? {}).length && (
        <details>
          <summary>公共采集来源与缺口</summary>
          {Object.entries(publicData.sourceResults ?? {}).map(([key, row]) => (
            <p key={key}>
              {(
                {
                  AKSHARE_MONEY_SUPPLY: "货币供应数据",
                  AKSHARE_CN_T10: "中国国债公开数据",
                  AKSHARE_SHIBOR_7D: "Shibor公开数据",
                  AKSHARE_HOUSE_HAINAN: "海口与三亚住房指数",
                  AKSHARE_HOUSE_MAINLAND: "上海与深圳住房指数",
                  CLS_TELEGRAPH: "财联社电报",
                  POLYMARKET_GAMMA: "Polymarket市场隐含赔率",
                  SPACEDEVS_LAUNCHES: "Launch Library发射计划",
                  GOV_SH_PBOC_TSF: "官方社融数据",
                } as Record<string, string>
              )[key] ?? "其他公共来源"}
              ：
              {(
                {
                  SUCCESS: "本次采集完成",
                  UNAVAILABLE: "暂无可用数据",
                  FAILED: "采集失败",
                  TIMEOUT: "来源超时",
                  PERMISSION_REQUIRED: "需要来源授权",
                  RATE_LIMITED: "来源限流",
                  SKIPPED: "本次未采集",
                } as Record<string, string>
              )[row.status] ?? "来源状态待核对"}{" "}
              · 获取：{timeLabel(row.retrievedAt)}
              {row.limitations?.map((text, i) => (
                <span key={i}>；{text}</span>
              ))}
            </p>
          ))}
        </details>
      )}
    </section>
  );
}

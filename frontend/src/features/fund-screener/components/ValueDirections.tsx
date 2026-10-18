import { useState } from "react";
import type {
  ActionableZoneResult,
  DailyResearch,
  PlanTrack,
  ValueDirection,
} from "../../../api/investment";
import { sourceHref, timeLabel } from "../macro-display";
import { planPrice, planUnit } from "../plan-display";
import { readingText } from "../research-reading";
import ActionablePlan from "./ActionablePlan";

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
const revisionLabels = {
  INITIAL: "初始关注区间",
  UPGRADED: "上调关注区间",
  DOWNGRADED: "下调关注区间",
  TAKE_PROFIT_ALERT: "止盈复核提示",
};

function FundPlan({
  plan,
  direction,
  reportRunId,
  onTrack,
}: {
  plan: ActionableZoneResult;
  direction: ValueDirection;
  reportRunId?: string | null;
  onTrack?: (input: DirectionTrackInput) => Promise<void>;
}) {
  const [state, setState] = useState<"idle" | "pending" | "saved">("idle");
  const [error, setError] = useState("");
  const selected = plan.selectedTrack ?? plan.track;
  const track = plan.tracks.find((item) => item.track === selected);
  const price = (value: number | string | null | undefined) =>
    planPrice(value, plan);
  const current = Number(plan.currentPrice);
  const inZone =
    plan.currentPrice != null &&
    track?.entryZoneMin != null &&
    track.entryZoneMax != null &&
    current >= Number(track.entryZoneMin) &&
    current <= Number(track.entryZoneMax);
  const canTrack =
    !!reportRunId &&
    !!onTrack &&
    !!selected &&
    !!track &&
    track.state !== "INSUFFICIENT" &&
    track.entryZoneMin != null &&
    track.entryZoneMax != null;
  const code = plan.fundCode || plan.subjectId;
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
    <article className="op-value-fund" aria-label={`${plan.label}买卖计划`}>
      <h4>
        {readingText(plan.label, 70)} <span>{code}</span>
      </h4>
      <p className="op-value-current">
        {plan.priceBasis === "UNIT_NAV" ? "最新确认净值" : "最新实际点位"}：
        <strong>{price(plan.currentPrice)}</strong> {planUnit(plan.unit)}
        {inZone && <span className="op-value-hit">已进入观察买入区</span>}
      </p>
      {track ? (
        <dl className="op-value-prices">
          <div>
            <dt>建议买入区间</dt>
            <dd>
              {price(track.entryZoneMin)}–{price(track.entryZoneMax)}
            </dd>
          </div>
          <div className="op-value-stop">
            <dt>止损参考</dt>
            <dd>{price(track.stopLossPrice)}</dd>
          </div>
          <div>
            <dt>止盈目标一</dt>
            <dd>{price(track.takeProfitT1)}</dd>
          </div>
          <div>
            <dt>止盈目标二</dt>
            <dd>{price(track.takeProfitT2)}</dd>
          </div>
        </dl>
      ) : (
        <p>等待真实价格依据</p>
      )}
      <small>
        {planUnit(plan.unit)} · {timeLabel(plan.asOf)} ·{" "}
        {selected === "LEFT_VALUE" ? "左侧" : "右侧"}研究坐标
      </small>
      {(plan.priceRevisionStatus || plan.priceRevisionNote) && (
        <p
          className="op-value-revision"
          role="status"
          aria-label="价格计划调整"
        >
          {plan.priceRevisionStatus &&
            `计划调整：${revisionLabels[plan.priceRevisionStatus]}。`}
          {readingText(plan.priceRevisionNote ?? "", 140)}
        </p>
      )}
      {plan.calibrationNote && (
        <p>复盘校准：{readingText(plan.calibrationNote, 140)}</p>
      )}
      {plan.volatilityMethod === "NAV_ABS_CHANGE_STD_V1" && (
        <small>
          净值波动风险法 · 14期净值波动尺度{" "}
          {price(plan.indicators.navVolatility14)}
        </small>
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
        <summary>价格依据与风险条件</summary>
        <ActionablePlan
          result={{ ...plan, tracks: track ? [track] : plan.tracks }}
        />
      </details>
    </article>
  );
}

export default function ValueDirections({
  data,
  onTrack,
}: {
  data?: DailyResearch | null;
  onTrack?: (input: DirectionTrackInput) => Promise<void>;
}) {
  const report = data?.report;
  if (!report) return null;
  const directions = report.valueDirections;
  return (
    <section
      className="op-value-directions"
      aria-label="今日价值方向与买卖计划"
    >
      <header>
        <h3>
          {data?.state === "READY"
            ? "今日价值方向与买卖计划"
            : "历史价值方向与买卖计划"}
        </h3>
        <small>08:30综合研究 · 交易日14:30核对场外基金</small>
      </header>
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
              </header>
              <p>{readingText(direction.thesis, 260)}</p>
              <details>
                <summary>综合依据与后续观察</summary>
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
                            {readingText(source.title, 100)}
                          </a>
                        ) : (
                          readingText(source.title, 100)
                        )}
                      </p>
                    )
                  );
                })}
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
          <p>此份历史格式报告未生成综合方向，保留原始研究计划。</p>
          {report.actionablePlans?.map((plan) => (
            <ActionablePlan
              key={`${plan.subjectKind}:${plan.subjectId}:${plan.selectedTrack ?? plan.track}`}
              result={plan}
            />
          ))}
        </>
      )}
    </section>
  );
}

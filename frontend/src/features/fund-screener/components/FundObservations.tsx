import { useState } from "react";
import type {
  DailyResearch,
  FundObservationItem,
  FundObservationTrack,
  FundObservationRisk,
  PlanPrice,
} from "../../../api/investment";
import { sourceHref, timeLabel } from "../macro-display";
import { fundDisplayName } from "../plan-display";
import { IndustryCycleBadge } from "./IndustryCycle";

const box = {
  border: "1px solid var(--op-line)",
  borderRadius: 10,
  padding: 14,
  marginBottom: 16,
  overflowWrap: "anywhere" as const,
};
const statusLabels = {
  OBSERVABLE: "技术资料可供观察",
  PRICE_INCOMPLETE: "参考价格依据不完整",
  NOT_SCANNED: "本次未完成扫描",
  IDENTITY_UNVERIFIED: "基金身份待核实",
  STALE: "资料已过期，等待更新",
};
const checks: Record<string, string> = {
  FINANCING_COOLING: "融资降温",
  LAYOFFS_CLOSURES: "欠薪裁员倒闭",
  ORDER_CUTS: "下游砍单",
  OVERCAPACITY_PRICE_WAR: "产能过剩与价格战",
  PRODUCTION_DELAY: "量产推迟",
  NO_CURRENT_EARNINGS_PROOF: "当期盈利兑现证据不足",
  EARLY_COMMERCIALIZATION: "商业化早期",
  FORWARD_PLANS_ONLY: "仅有远期计划",
  PERSISTED_RISK_WATCH: "延续历史风险观察",
  REFERENCE_TRACK_UNAVAILABLE: "参考轨道依据不足",
  FUND_IDENTITY_UNVERIFIED: "基金身份尚未核实",
  COMPLETE_REFERENCE_PRICE_UNAVAILABLE: "完整参考价格轨道不足",
  SUBSCRIPTION_STATUS_UNKNOWN: "申购状态尚未核实",
  INDUSTRY_AUDIT_PENDING_PROOF: "行业反方审核凭证待补齐",
  FROZEN_PRICE_REFERENCE_STALE: "冻结价格参考已过期",
  FROZEN_OBSERVATION_INVALID: "冻结基金观察数据未通过校验",
  NAV_PROVENANCE_UNVERIFIED: "净值来源与证据时间尚未核实",
  INVALID_SCAN_STRUCTURE: "冻结扫描结构未通过校验",
};
function number(value: PlanPrice | undefined, digits = 4) {
  return value != null && value !== "" && Number.isFinite(Number(value))
    ? Number(value).toFixed(digits)
    : "未知";
}
function Risk({
  cycle,
  historical = false,
}: {
  cycle: FundObservationRisk;
  historical?: boolean;
}) {
  const flags = [
    ...new Set([
      ...(cycle.riskFlags ?? []),
      ...(cycle.confirmedRiskFlags ?? []),
      ...(cycle.suspectedRiskFlags ?? []),
      ...(cycle.persistedRiskFlags ?? []),
    ]),
  ];
  return (
    <section aria-label={historical ? "历史行业风险" : "当前行业风险"}>
      <p>
        <strong>
          {historical ? "历史行业风险" : "当前行业风险"} · {cycle.industry}
        </strong>{" "}
        · {timeLabel(cycle.researchedAt)}
        {historical && (cycle.active ? " · 仍在观察" : " · 原始历史记录")}
      </p>
      <IndustryCycleBadge cycle={cycle} />
      {cycle.evidenceSummary && <p>{cycle.evidenceSummary}</p>}
      {!!flags.length && (
        <p>
          风险标记：
          {flags.map((flag) => checks[flag] ?? flag).join("、")}
        </p>
      )}
      {Object.entries(cycle.confirmedRiskAt ?? {}).map(([flag, at]) => (
        <p key={flag}>
          {historical ? "历史风险原始确认" : "风险原始确认"}：
          {checks[flag] ?? flag} · {timeLabel(at)}
        </p>
      ))}
      {!!cycle.evidenceGaps?.length && (
        <p>
          证据缺口：
          {cycle.evidenceGaps.map((gap) => checks[gap] ?? gap).join("、")}
        </p>
      )}
    </section>
  );
}
function Track({ track }: { track: FundObservationTrack }) {
  const technical =
    track.observationStatus === "TECHNICAL_IN_ZONE"
      ? "技术位置在参考区内（非买入许可）"
      : track.observationStatus === "STALE"
        ? "技术资料已过期"
        : track.observationStatus === "UNAVAILABLE"
          ? "参考轨道依据不足"
          : track.observationStatus === "AWAY"
            ? "技术位置在参考区外"
            : "技术条件待核实或待触发";
  return (
    <section
      aria-label={
        track.track === "LEFT_VALUE" ? "左侧技术参考" : "右侧技术参考"
      }
    >
      <h5>
        {track.track === "LEFT_VALUE" ? "左侧" : "右侧"}技术参考 · {technical}
      </h5>
      {(track.entryStatus || track.entryBlocked || track.inSellZone) && (
        <p
          role="status"
          className={
            track.inSellZone ? "op-sell-zone-conclusion" : "op-entry-warning"
          }
        >
          服务器保护状态：
          {track.entryStatus ||
            (track.inSellZone
              ? "已进入高位退出观察区，不能据此推定可入场。"
              : "暂缓入场，风险保护尚未解除。")}
        </p>
      )}
      <p>
        原参考区：{number(track.entryZoneMin)}–{number(track.entryZoneMax)} ·
        距参考区：{number(track.distanceToEntryPct, 2)}%
      </p>
      <p>
        风险退出参考：{number(track.stopLossPrice)} · 高位退出观察参考 T1 / T2：
        {number(track.takeProfitT1)} / {number(track.takeProfitT2)}
      </p>
      <p>
        RSI14：{number(track.metrics.rsi14, 2)} · 相对沪深300超额 20日 / 60日：
        {number(track.metrics.excessReturn20d, 2)} /{" "}
        {number(track.metrics.excessReturn60d, 2)} 个百分点 · 60日高点回落：
        {number(track.metrics.drawdown60d, 2)}%
      </p>
      {track.technicalState === "CANDIDATE" && (
        <p>技术条件成立不代表获得正式投资资格；高位退出参考须单独核对。</p>
      )}
      {!!track.missingEvidence.length && (
        <p>
          轨道缺口：
          {track.missingEvidence.map((gap) => checks[gap] ?? gap).join("、")}
        </p>
      )}
    </section>
  );
}
function FundDetails({ item }: { item: FundObservationItem }) {
  const nav = item.confirmedNav;
  const href = sourceHref(nav?.sourceUri);
  return (
    <>
      <p>
        {statusLabels[item.observationStatus]} ·{" "}
        {item.identityVerified
          ? "冻结服务器目录身份已核对"
          : "基金身份尚未核实"}
      </p>
      <p>
        最新确认净值：{number(nav?.value)} 元/份 · 净值日期：
        {timeLabel(nav?.asOf)}
        {href && (
          <>
            {" "}
            ·{" "}
            <a href={href} target="_blank" rel="noreferrer">
              净值来源
            </a>
          </>
        )}
      </p>
      <p>
        {
          {
            UNKNOWN: "申购状态未知，不推定可申购",
            OPEN: "已披露开放申购，仍须核对最新限制",
            LIMITED: "限额申购，须核对最新限制",
            SUSPENDED: "暂停申购",
          }[item.subscription.status]
        }
      </p>
      {item.referenceTracks.map((track) => (
        <Track key={track.track} track={track} />
      ))}
      {(["LEFT_VALUE", "RIGHT_TREND"] as const)
        .filter(
          (track) => !item.referenceTracks.some((row) => row.track === track),
        )
        .map((track) => (
          <p key={track}>
            {track === "LEFT_VALUE" ? "左侧" : "右侧"}
            参考轨道缺失，不能推定价格完整。
          </p>
        ))}
      <p>
        行业：{item.industry.industryIds.join("、") || "尚未核定"} ·{" "}
        {
          {
            NOT_ALLOCATED: "尚未分配行业反方审核",
            PENDING_PROOF: "行业反方审核凭证待补齐",
            STRUCTURE_AND_EXECUTION_VERIFIED:
              "行业审核结构与执行已核验，仍须逐项核对风险证据",
          }[item.industry.auditStatus]
        }
      </p>
      {!!item.industry.missingChecks.length && (
        <p>
          待补行业核查：
          {item.industry.missingChecks
            .map((kind) => checks[kind] ?? kind)
            .join("、")}
        </p>
      )}
      {item.risk.current.map((cycle, index) => (
        <Risk key={`current-${index}`} cycle={cycle} />
      ))}
      {item.risk.historical.map((cycle, index) => (
        <Risk key={`historical-${index}`} cycle={cycle} historical />
      ))}
      {!!item.topicAssociations.length && (
        <section aria-label="同次研究主题关联">
          {item.topicAssociations.map((topic, index) => (
            <p key={`${topic.key}-${index}`}>
              <span>{topic.title}</span> ·{" "}
              {topic.relation === "CONFIRMED_SCOPE_LINK"
                ? "行业范围关联已核实，非投资许可"
                : "仅声明相关，行业范围关联未核实"}
              {!!topic.sourceIds.length && (
                <> · 同次冻结来源：{topic.sourceIds.join("、")}</>
              )}
            </p>
          ))}
        </section>
      )}
      {!!item.relativeStrength?.missingEvidence?.length && (
        <p>
          相对强弱资料缺口：{item.relativeStrength.missingEvidence.join("、")}
        </p>
      )}
      {!!item.missingEvidence.length && (
        <p>
          资料缺口：
          {item.missingEvidence.map((gap) => checks[gap] ?? gap).join("、")}
        </p>
      )}
      <p>
        <small>
          仅供只读观察，尚未获得正式投资资格。参考区和技术入区均不是买入建议。
        </small>
      </p>
    </>
  );
}
function InventoryItem({ item }: { item: FundObservationItem }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <details onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary>
          {fundDisplayName(item.name, item.fundCode)}{" "}
          <span>{item.fundCode}</span> · {statusLabels[item.observationStatus]}{" "}
          · 确认净值 {number(item.confirmedNav?.value)}（
          {timeLabel(item.confirmedNav?.asOf)}）
        </summary>
        {open && <FundDetails item={item} />}
      </details>
    </li>
  );
}
export default function FundObservations({
  data,
}: {
  data?: DailyResearch | null;
}) {
  const observation = data?.fundObservation;
  const [inventoryOpen, setInventoryOpen] = useState(false);
  if (!observation) return null;
  const currentId = data?.pipeline ? data.pipeline.researchRunId : data?.runId;
  const current =
    typeof currentId === "string" &&
    !!currentId &&
    observation.researchId === currentId;
  const retained =
    !!data?.report &&
    !!data.reportRunId &&
    observation.researchId === data.reportRunId;
  const valid =
    observation.version === "fund-observation-v1" &&
    observation.actionable === false &&
    observation.investmentEligible === false &&
    (current || retained);
  return (
    <section aria-label="服务器代表池基金观察" style={box}>
      <h3>服务器代表池基金观察 · 只读</h3>
      {!valid ? (
        <p role="alert">基金观察身份不一致，暂不展示内容。</p>
      ) : (
        <>
          <p>
            研究ID：{observation.researchId} · 研究证据：
            {timeLabel(observation.researchAsOf)} · 观察核对：
            {timeLabel(observation.evaluatedAt)}
          </p>
          {!current && (
            <p>
              此为最近成功报告对应的保留观察，日期保持原样，不代表当前研究已更新。
            </p>
          )}
          {observation.status === "STALE" && (
            <p role="status">
              观察资料已过期，等待更新；原参考区不代表当前有效价格。
            </p>
          )}
          {observation.status === "UNAVAILABLE" && (
            <p role="status">基金观察资料不足；不能据此推断没有基金或风险。</p>
          )}
          <p>
            服务器代表池 {observation.coverage.catalogFundCount}只 · 已扫描{" "}
            {observation.coverage.scannedFundCount}只 · 有确认净值{" "}
            {observation.coverage.confirmedNavFundCount}只 · 有完整参考轨道{" "}
            {observation.coverage.pricedFundCount}只 · 可观察{" "}
            {observation.coverage.observableFundCount}只 · 价格依据不足{" "}
            {observation.coverage.unpricedFundCount}只
          </p>
          <p>
            本批审核行业 {observation.coverage.selectedScopeCount}个 · 延后{" "}
            {observation.coverage.deferredScopeCount}个 · 有执行凭证的核查{" "}
            {observation.coverage.executionVerifiedCheckCount}
            项。代表池不是全市场穷尽覆盖，排序不代表推荐买入。
          </p>
          {observation.unresolvedRiskNotice && (
            <p role="status">{observation.unresolvedRiskNotice}</p>
          )}
          {!!observation.missingEvidence?.length && (
            <p>
              观察证据缺口：
              {observation.missingEvidence
                .map((gap) => checks[gap] ?? gap)
                .join("、")}
            </p>
          )}
          {!!observation.unresolvedRiskScopes.length && (
            <p>
              未完成风险核验的行业：
              {observation.unresolvedRiskScopes.join("、")}
            </p>
          )}
          <section aria-label="重点基金观察">
            <h4>重点观察 · 最多10只</h4>
            {observation.highlights.slice(0, 10).map((item) => (
              <article
                key={`${item.fundCode}:${item.subjectKind}:${item.subjectId}`}
                aria-label={`${fundDisplayName(item.name, item.fundCode)} ${item.fundCode}`}
                style={box}
              >
                <h4>
                  {fundDisplayName(item.name, item.fundCode)}{" "}
                  <span>{item.fundCode}</span>
                </h4>
                <FundDetails item={item} />
              </article>
            ))}
            {!observation.highlights.length && (
              <p>尚无资料充分的重点观察基金，完整代表池及其缺口仍保留。</p>
            )}
          </section>
          <details
            onToggle={(event) => setInventoryOpen(event.currentTarget.open)}
          >
            <summary>查看完整代表池 · {observation.items.length}只基金</summary>
            {inventoryOpen && (
              <ul>
                {observation.items.map((item) => (
                  <InventoryItem
                    key={`${item.fundCode}:${item.subjectKind}:${item.subjectId}`}
                    item={item}
                  />
                ))}
              </ul>
            )}
          </details>
        </>
      )}
    </section>
  );
}

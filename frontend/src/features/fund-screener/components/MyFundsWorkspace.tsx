import { useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  getStudyDetail,
  setFundWatchStatus,
  setStudyStatus,
  type StudyPriceWatch,
  type StudyTrack,
} from "../../../api/study-api";
import {
  personalWatchState,
  relationshipLabels,
  type PersonalFundGroup,
} from "../personal-fund-model";
import { usePersonalFunds } from "../use-personal-funds";
import { fundDisplayName, planPrice, planUnit } from "../plan-display";
import { sourceHref, timeLabel } from "../macro-display";
import { currentFundEstimate, watchStateLabels } from "../watch-display";
import {
  refreshLatestFundQuotes,
  useLatestFundQuote,
} from "../latest-quote-resource";
import { studyConditionLabel, studyStates } from "./study-display";
import { FundWatchQuote } from "./PriceWatchSummary";
import ViewportFundChart from "./ViewportFundChart";
import FundReferenceDetails from "./FundReferenceDetails";
import "../my-funds.css";

export interface MyFundsWorkspaceProps {
  actor: string;
  renderStudyTracking?: () => ReactNode;
}

// The frozen-study price watcher accepts these equivalent yuan-per-unit labels.
const navUnits = new Set([
  "CNY",
  "元",
  "元/份",
  "CNY/unit",
  "CNY/份",
  "CNY/share",
]);

function newest<T extends { asOf: string | null; value: string | null }>(
  rows: Array<T | null | undefined>,
) {
  return rows.reduce<T | null>(
    (latest, row) =>
      row?.value != null && (!latest || (row.asOf ?? "") > (latest.asOf ?? ""))
        ? row
        : latest,
    null,
  );
}

function FundCard({
  group,
  actor,
  onReview,
  onCancel,
  pending,
}: {
  group: PersonalFundGroup;
  actor: string;
  onReview: (id: string) => void;
  onCancel: () => void;
  pending: boolean;
}) {
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const chosen =
    group.tracks.find((row) => row.id === chosenId) ?? group.tracks[0];
  const detail = useQuery({
    queryKey: ["study", actor, "detail", detailId, 0],
    queryFn: ({ signal }) => getStudyDetail(detailId!, signal),
    enabled: !!detailId,
  });
  const publicCode =
    group.market === "FUND_CN" && /^\d{6}$/.test(group.code ?? "")
      ? group.code
      : undefined;
  const latest = useLatestFundQuote(publicCode);
  const watches = group.tracks.flatMap((row) =>
    row.priceWatch ? [row.priceWatch] : [],
  );
  const confirmed = newest<NonNullable<StudyPriceWatch["confirmedNav"]>>([
    latest.data?.confirmedNav,
    group.holding?.confirmedNav,
    ...watches.map((row) => row.confirmedNav),
  ]);
  const estimate = newest<NonNullable<StudyPriceWatch["currentEstimate"]>>([
    latest.data?.currentEstimate,
    group.holding?.currentEstimate,
    ...group.tracks.map((row) => row.currentEstimate),
    ...watches.map(currentFundEstimate),
  ]);
  const watch = chosen?.priceWatch;
  const plan = watch?.frozenPlan;
  const frozenTrack = plan?.tracks.find(
    (row) => row.track === (plan.selectedTrack ?? plan.track),
  );
  const overlay =
    plan?.priceBasis === "UNIT_NAV" && navUnits.has(plan.unit ?? "")
      ? frozenTrack
      : undefined;
  const state = personalWatchState(watch);
  const chosenDetail =
    detail.data?.track.id === chosen?.id ? detail.data : undefined;
  function select(track: StudyTrack) {
    setChosenId(track.id);
    setDetailId(track.id);
  }
  return (
    <article className="op-my-fund-card" aria-label={group.label}>
      <div className="op-my-fund-info">
        <header>
          <h4>{fundDisplayName(group.label, group.code)}</h4>
          <small>
            {group.code ?? "代码待核对"} ·{" "}
            {group.market === "UNKNOWN" ? "市场待核对" : group.market}
          </small>
          <div className="op-my-fund-badges">
            {group.holding && <span>已持仓</span>}
            {!!group.tracks.length && (
              <span>
                {group.holding ? "同时跟踪" : "仅跟踪"} · {group.tracks.length}
                份独立研究
              </span>
            )}
            {group.trackingSuppressed && <span>已取消跟踪</span>}
            {group.reference?.relationships
              .filter(
                (key) => key === "WATCHLIST" || key === "RESEARCH_MENTIONED",
              )
              .map((key) => (
                <span key={key}>{relationshipLabels[key]}</span>
              ))}
          </div>
        </header>
        <FundWatchQuote row={confirmed} label="最新确认净值" />
        <FundWatchQuote
          row={estimate}
          label="可用估算净值（非最终净值）"
          estimate
        />
        {estimate?.checkedAt && (
          <small>估值核对时间：{timeLabel(estimate.checkedAt)}</small>
        )}
        {latest.failed && (
          <p role="status">独立行情更新失败，保留已有记录及原日期。</p>
        )}
        {group.reference && <FundReferenceDetails fund={group.reference} />}
        {!!group.tracks.length && (
          <div className="op-my-fund-plans" aria-label="独立冻结研究计划">
            {group.tracks.map((track) => (
              <button
                type="button"
                key={track.id}
                aria-pressed={chosen?.id === track.id}
                aria-label={`选择计划 ${track.title}`}
                onClick={() => select(track)}
              >
                {track.title} · {timeLabel(track.createdAt)}
              </button>
            ))}
          </div>
        )}
        {chosen ? (
          <section aria-label="当前选择的冻结计划" className="op-my-fund-plan">
            <strong>{chosen.title}</strong>
            <small>
              研究建立时间：{timeLabel(chosen.createdAt)} ·{" "}
              {studyStates[chosen.status] ?? "状态待核对"}
            </small>
            {plan && (
              <small>
                计划行情基准：{timeLabel(plan.asOf)} · {plan.priceBasis} ·{" "}
                {planUnit(plan.unit)} · {plan.methodVersion}
                {sourceHref(plan.sourceUri) && (
                  <>
                    {" "}
                    ·{" "}
                    <a
                      href={sourceHref(plan.sourceUri)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      原计划来源
                    </a>
                  </>
                )}
              </small>
            )}
            {frozenTrack ? (
              <>
                <p>
                  原冻结买入区间：
                  <strong>
                    {planPrice(frozenTrack.entryZoneMin, plan!)}–
                    {planPrice(frozenTrack.entryZoneMax, plan!)}{" "}
                    {planUnit(plan!.unit)}
                  </strong>
                </p>
                <p>
                  止损 {planPrice(frozenTrack.stopLossPrice, plan!)} · T1{" "}
                  {planPrice(frozenTrack.takeProfitT1, plan!)} / T2{" "}
                  {planPrice(frozenTrack.takeProfitT2, plan!)}
                </p>
                <small>
                  冻结轨道：
                  {frozenTrack.track === "LEFT_VALUE"
                    ? "左侧低吸"
                    : "右侧趋势"}{" "}
                  · 研究时状态：
                  {frozenTrack.state === "CANDIDATE"
                    ? "候选"
                    : frozenTrack.state === "WATCH"
                      ? "观察"
                      : "资料待核对"}
                  。价位为原研究参考，未随最新行情改写。
                </small>
              </>
            ) : (
              <p>尚无完整冻结买卖点。</p>
            )}
            <p
              className={`op-my-fund-state state-${state?.toLowerCase() ?? "unknown"}`}
            >
              <strong>{state ? watchStateLabels[state] : "行情待更新"}</strong>
            </p>
            <small>
              当前判断依据：
              {watch?.basis === "CONFIRMED_NAV"
                ? "确认净值"
                : watch?.basis === "ESTIMATE_1430"
                  ? "14:30估算净值"
                  : watch?.basis === "ESTIMATE_INTRADAY"
                    ? "盘中估算净值"
                    : "行情待更新"}{" "}
              · {timeLabel(watch?.asOf)}
            </small>
            {watch?.reason && <p>{watch.reason}</p>}
            {chosenDetail ? (
              <p>
                下一观察条件：
                {chosenDetail.version.snapshot.watchFor[0] ??
                  (chosenDetail.version.snapshot.conditions[0]
                    ? studyConditionLabel(
                        chosenDetail.version.snapshot.conditions[0],
                      )
                    : "未声明，需人工复盘。")}{" "}
                <small>
                  版本 {chosenDetail.version.version} ·{" "}
                  {timeLabel(chosenDetail.version.createdAt)}
                </small>
              </p>
            ) : detailId === chosen.id && detail.isError ? (
              <p role="alert">
                该研究条件读取失败，未借用其他研究。
                <button type="button" onClick={() => void detail.refetch()}>
                  重试观察条件
                </button>
              </p>
            ) : detailId === chosen.id && detail.isPending ? (
              <p role="status">正在读取所选研究条件…</p>
            ) : (
              <p>
                选择计划或
                <button type="button" onClick={() => setDetailId(chosen.id)}>
                  读取下一观察条件
                </button>
                后查看原研究条件。
              </p>
            )}
            <button type="button" onClick={() => onReview(chosen.id)}>
              研究与复盘：{chosen.title}
            </button>
          </section>
        ) : (
          <p>
            {group.reference?.plan
              ? "当前程序参考价位独立展示；尚未保存个人冻结买卖计划。"
              : "持仓观察；尚未保存冻结买卖计划，未推算买卖条件。"}
          </p>
        )}
        {!group.trackingSuppressed &&
          (group.holding || group.tracks.length > 0) && (
            <button
              type="button"
              disabled={pending}
              onClick={onCancel}
              aria-label={`取消跟踪 ${group.code ?? group.label}`}
            >
              {pending ? "取消中…" : "取消跟踪"}
            </button>
          )}
      </div>
      {!group.reference && (
        <div className="op-my-fund-chart">
          {publicCode ? (
            <ViewportFundChart
              fundCode={publicCode}
              track={overlay}
              confirmedNavAsOf={confirmed?.asOf ?? undefined}
            />
          ) : (
            <p className="op-my-fund-chart-empty">
              净值图暂不可用：
              {group.code
                ? "当前市场尚未接入对应真实净值曲线。"
                : "基金代码尚未确认。"}
            </p>
          )}
          {!plan && publicCode && (
            <small>当前仅展示真实净值；尚无冻结买卖点。</small>
          )}
          {plan && !overlay && publicCode && (
            <small>原计划口径或单位未确认匹配，曲线不叠加该价位。</small>
          )}
        </div>
      )}
    </article>
  );
}

export default function MyFundsWorkspace(props: MyFundsWorkspaceProps) {
  return <Workspace key={props.actor} {...props} />;
}

function Workspace({ actor, renderStudyTracking }: MyFundsWorkspaceProps) {
  const { list, reference, groups } = usePersonalFunds(actor);
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const term = search.trim().toLocaleLowerCase();
  const matches = (row: PersonalFundGroup) =>
    !term ||
    `${row.label} ${row.code ?? ""} ${row.holding?.instrumentId ?? ""}`
      .toLocaleLowerCase()
      .includes(term);
  const filtered = groups.filter(
    (row) =>
      (filter !== "held" || row.holding) &&
      (filter !== "tracked" ||
        (!row.holding &&
          (row.tracks.length ||
            row.reference?.relationships.includes("TRACKED")))) &&
      (filter !== "watchlist" ||
        row.reference?.relationships.includes("WATCHLIST")) &&
      (filter !== "mentioned" ||
        row.reference?.relationships.includes("RESEARCH_MENTIONED")) &&
      matches(row),
  );
  const compact = groups.length > 4;
  const chosenGroup = filtered.find((row) => row.key === selected);
  const cards = compact ? (chosenGroup ? [chosenGroup] : []) : filtered;
  const history = (list.data?.items ?? []).filter(
    (track) =>
      !term ||
      `${track.title} ${track.scope.fundCode ?? ""} ${track.scope.instrumentId ?? ""}`
        .toLocaleLowerCase()
        .includes(term),
  );
  function review(id: string) {
    setReviewOpen(true);
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("studyTrack", id);
      next.set("researchTab", "tracking");
      next.set("researchOpen", "1");
      next.delete("researchData");
      return next;
    });
  }
  async function cancel(group: PersonalFundGroup) {
    if (pending) return;
    setPending(group.key);
    setError("");
    setNotice("");
    try {
      const knownMarket =
        group.market !== "UNKNOWN" &&
        /^[A-Z][A-Z0-9_]{0,31}$/.test(group.market);
      if (knownMarket && /^\d{6}$/.test(group.code ?? ""))
        await setFundWatchStatus({
          fundCode: group.code,
          fundMarket: group.market,
          enabled: false,
        });
      else if (group.tracks.length)
        for (const track of group.tracks)
          await setStudyStatus(track.id, {
            expectedGeneration: track.generation,
            enabled: false,
          });
      else if (knownMarket && group.holding?.instrumentId)
        await setFundWatchStatus({
          instrumentId: group.holding.instrumentId,
          fundMarket: group.market,
          enabled: false,
        });
      else
        throw new Error(
          "基金市场或标的身份尚未确认，无法取消跟踪；持仓记录保留。",
        );
      setNotice(
        group.holding
          ? "已取消跟踪，持仓记录保留。"
          : "已取消跟踪，历史研究记录保留。",
      );
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "取消跟踪失败，请重试。",
      );
    } finally {
      // A preceding study may already be disabled when a later request fails.
      // Refresh its generation without replacing the original mutation error.
      await client
        .invalidateQueries({ queryKey: ["study", actor] })
        .catch(() => undefined);
      await reference.refetch().catch(() => undefined);
      setPending(null);
    }
  }
  const showReview = reviewOpen || !!params.get("studyTrack");
  return (
    <section className="op-my-funds" aria-label="我的基金">
      <header className="op-my-funds-heading">
        <div>
          <h3>我的基金</h3>
          <p>
            合并持仓、活动跟踪、自选与本期研究提及；当前程序价位和原冻结计划分别保留日期与来源。
          </p>
        </div>
        <button
          type="button"
          disabled={list.isFetching}
          onClick={() => {
            refreshLatestFundQuotes();
            void list.refetch();
            void reference.refetch();
          }}
        >
          刷新我的基金
        </button>
      </header>
      <div className="op-my-funds-toolbar">
        <nav aria-label="我的基金筛选">
          {[
            ["all", "全部"],
            ["held", "已持仓"],
            ["tracked", "仅跟踪"],
            ["watchlist", "自选关注"],
            ["mentioned", "本期提及"],
            ["history", "历史复盘"],
          ].map(([key, label]) => (
            <button
              type="button"
              key={key}
              aria-pressed={filter === key}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
        </nav>
        <input
          type="search"
          aria-label="搜索我的基金"
          placeholder="搜索基金名称或代码"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      {list.isPending && <p role="status">正在读取我的基金…</p>}
      {list.isError && (
        <p role="alert">
          我的基金读取失败；已有记录可能尚未更新。
          <button type="button" onClick={() => void list.refetch()}>
            重试我的基金
          </button>
        </p>
      )}
      {reference.isError && (
        <p role="status">
          自选与本期提及基金暂未补齐，已有持仓和跟踪仍可查看。
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {filter === "history" ? (
        <section aria-label="全部研究与历史复盘">
          <p>包含活动、已结束、停用及非基金研究；原版本和复盘继续保留。</p>
          <ul className="op-my-funds-history">
            {history.map((track) => (
              <li key={track.id}>
                <button type="button" onClick={() => review(track.id)}>
                  {track.title} · {studyStates[track.status] ?? "状态待核对"}
                  {!track.enabled ? " · 已停用" : ""} ·{" "}
                  {timeLabel(track.createdAt)}
                </button>
              </li>
            ))}
          </ul>
          {list.isSuccess && !history.length && <p>暂无匹配的历史研究。</p>}
        </section>
      ) : (
        <>
          {compact && (
            <>
              <ul className="op-my-funds-list" aria-label="基金紧凑列表">
                {filtered.map((group) => (
                  <li key={group.key}>
                    <button
                      type="button"
                      aria-pressed={selected === group.key}
                      onClick={() => setSelected(group.key)}
                    >
                      <strong>
                        {fundDisplayName(group.label, group.code)}
                      </strong>
                      <span>
                        {group.code ?? "代码待核对"} · {group.market} ·{" "}
                        {group.holding
                          ? "已持仓"
                          : group.tracks.length
                            ? "仅跟踪"
                            : group.reference?.relationships
                                .map((key) => relationshipLabels[key])
                                .join("、")}{" "}
                        · {group.tracks.length}份计划
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              {filtered.length > 0 && !chosenGroup && (
                <p>选择一只基金查看净值图与完整计划。</p>
              )}
            </>
          )}
          {cards.map((group) => (
            <FundCard
              key={group.key}
              group={group}
              actor={actor}
              onReview={review}
              onCancel={() => void cancel(group)}
              pending={pending !== null}
            />
          ))}
          {list.isSuccess && !reference.isPending && !filtered.length && (
            <p>
              {groups.length
                ? "没有匹配的基金，请调整筛选或搜索。"
                : "暂无持仓、活动跟踪、自选或本期提及基金；历史研究仍可查看。"}
            </p>
          )}
        </>
      )}
      {renderStudyTracking && (
        <section className="op-my-funds-review" aria-label="研究与复盘">
          <button
            type="button"
            aria-expanded={showReview}
            onClick={() => {
              if (showReview) {
                setReviewOpen(false);
                setParams((previous) => {
                  const next = new URLSearchParams(previous);
                  next.delete("studyTrack");
                  return next;
                });
              } else setReviewOpen(true);
            }}
          >
            {showReview ? "收起研究与复盘" : "打开研究与复盘 / 新建研究"}
          </button>
          {showReview && renderStudyTracking()}
        </section>
      )}
    </section>
  );
}

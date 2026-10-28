import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  getStudyTracks,
  setFundWatchStatus,
  setStudyStatus,
  type StudyTrack,
  type HeldFundWatch,
  type StudyPriceWatch,
} from "../../../api/study-api";
import { fundDisplayName, planPrice } from "../plan-display";
import { studyStates } from "./study-display";
import { FundWatchQuote } from "./PriceWatchSummary";
import { currentFundEstimate, watchStateLabels } from "../watch-display";
import { useStudyQuoteRefresh } from "../use-study-tracking";
import {
  refreshLatestFundQuotes,
  useLatestFundQuote,
} from "../latest-quote-resource";

const shanghaiDate = (time: number) =>
  new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(time));

interface FundRow {
  key: string;
  code?: string;
  market: string;
  label: string;
  holding?: HeldFundWatch;
  tracks: StudyTrack[];
}
function newest<T extends { asOf: string | null; value: string | null }>(
  rows: Array<T | null | undefined>,
): T | null {
  return rows.reduce<T | null>(
    (latest, row) =>
      row?.value != null && (!latest || (row.asOf ?? "") > (latest.asOf ?? ""))
        ? row
        : latest,
    null,
  );
}
function LiveFundWatchQuote({
  code,
  row,
  estimate = false,
}: {
  code?: string;
  row: StudyPriceWatch["currentEstimate"];
  estimate?: boolean;
}) {
  const latest = useLatestFundQuote(code);
  const current = estimate
    ? latest.data?.currentEstimate
    : latest.data?.confirmedNav;
  return <FundWatchQuote row={newest([current, row])} estimate={estimate} />;
}
function FrozenPlan({ track }: { track: StudyTrack }) {
  const plan = track.priceWatch?.frozenPlan;
  const selected = plan?.selectedTrack ?? plan?.track;
  const prices = plan?.tracks.find((row) => row.track === selected);
  const price = (value?: number | string | null) =>
    planPrice(value, plan ?? { subjectKind: "nav", priceBasis: "UNIT_NAV" });
  return (
    <div className="op-watch-plan">
      <small>{track.title}</small>
      {prices ? (
        <>
          <span>
            冻结买入区间：
            <strong>
              {price(prices.entryZoneMin)}–{price(prices.entryZoneMax)}
            </strong>
          </span>
          <span>止损 {price(prices.stopLossPrice)}</span>
          <span>
            T1 {price(prices.takeProfitT1)} / T2 {price(prices.takeProfitT2)}
          </span>
        </>
      ) : (
        <span>行情与冻结价位待核对</span>
      )}
    </div>
  );
}
export default function TrackedFundWatch({ actor }: { actor: string }) {
  useStudyQuoteRefresh(actor);
  const [, setParams] = useSearchParams();
  const client = useQueryClient();
  const [today, setToday] = useState(() => shanghaiDate(Date.now()));
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    const day = 24 * 60 * 60 * 1000;
    const offset = 8 * 60 * 60 * 1000;
    let timer: ReturnType<typeof setTimeout>;
    function schedule() {
      const now = Date.now();
      const midnight = (Math.floor((now + offset) / day) + 1) * day - offset;
      timer = setTimeout(() => {
        setToday(shanghaiDate(Date.now()));
        schedule();
      }, midnight - now);
    }
    schedule();
    return () => clearTimeout(timer);
  }, []);
  const list = useQuery({
    queryKey: ["study", actor, "list"],
    queryFn: ({ signal }) => getStudyTracks(signal),
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
  });
  const suppressed = new Set(list.data?.suppressedFundKeys ?? []);
  const groups = new Map<string, FundRow>();
  for (const holding of list.data?.heldFunds ?? []) {
    const instrumentKey = `${holding.fundMarket}:instrument:${holding.instrumentId}`;
    const key = holding.fundCode
      ? `${holding.fundMarket}:${holding.fundCode}`
      : instrumentKey;
    if (!suppressed.has(key) && !suppressed.has(instrumentKey))
      groups.set(key, {
        key,
        code: holding.fundCode ?? undefined,
        market: holding.fundMarket,
        label: holding.label,
        holding,
        tracks: [],
      });
  }
  for (const track of list.data?.items ?? []) {
    if (
      !track.enabled ||
      track.status === "ENDED" ||
      (track.expiresOn && track.expiresOn < today)
    )
      continue;
    const plan = track.priceWatch?.frozenPlan;
    if (plan?.subjectKind === "index") continue;
    const code =
      track.scope.fundCode ??
      plan?.fundCode ??
      (plan?.subjectKind === "nav" ? plan.subjectId : undefined);
    const identity =
      code ??
      track.scope.instrumentId ??
      (plan?.subjectKind === "fund" ? plan.subjectId : undefined);
    if (!identity) continue;
    const held = [...groups.values()].find(
      (row) =>
        track.scope.instrumentId &&
        row.holding?.instrumentId === track.scope.instrumentId,
    );
    const market =
      held?.market ?? track.scope.fundMarket ?? plan?.market ?? "FUND_CN";
    const key = held?.key ?? `${market}:${identity}`;
    if (
      suppressed.has(key) ||
      (track.scope.instrumentId &&
        suppressed.has(`${market}:instrument:${track.scope.instrumentId}`))
    )
      continue;
    const group = groups.get(key) ?? {
      key,
      code,
      market,
      label: plan?.label ?? track.title,
      tracks: [],
    };
    if (!group.tracks.some((item) => item.id === track.id))
      group.tracks.push(track);
    groups.set(key, group);
  }
  function review(id: string) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      next.set("studyTrack", id);
      next.set("researchTab", "tracking");
      next.set("researchOpen", "1");
      next.delete("researchData");
      return next;
    });
  }
  async function cancel(row: FundRow) {
    if (pending) return;
    setPending(row.key);
    setError("");
    setNotice("");
    try {
      if (row.code)
        await setFundWatchStatus({
          fundCode: row.code,
          fundMarket: row.market,
          enabled: false,
        });
      else if (row.holding)
        await setFundWatchStatus({
          instrumentId: row.holding.instrumentId,
          fundMarket: row.market,
          enabled: false,
        });
      else
        for (const track of row.tracks)
          await setStudyStatus(track.id, {
            expectedGeneration: track.generation,
            enabled: false,
          });
      await client.invalidateQueries({ queryKey: ["study", actor] });
      setNotice(
        row.holding
          ? "已取消跟踪，持仓记录保留。"
          : "已取消跟踪，历史研究记录保留。",
      );
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "取消跟踪失败，请重试。",
      );
    } finally {
      setPending(null);
    }
  }
  return (
    <section className="op-tracked-funds" aria-label="我的持续跟踪基金">
      <header>
        <h3>我的持续跟踪基金</h3>
        <button
          type="button"
          disabled={list.isFetching}
          onClick={() => {
            refreshLatestFundQuotes();
            void list.refetch();
          }}
        >
          刷新跟踪行情
        </button>
      </header>
      <p>
        包含手动跟踪与已导入的持仓基金；打开页面会更新行情，收盘后保留最新估值及其时间。估值仅供参考，条件触发后请核对证据与持仓。
      </p>
      {list.isPending && <p role="status">正在读取持续跟踪基金…</p>}
      {list.isError && (
        <p role="alert">
          持续跟踪基金读取失败，已显示的行情可能尚未更新。
          <button type="button" onClick={() => void list.refetch()}>
            重试持续跟踪基金
          </button>
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {list.isSuccess && !groups.size && (
        <p>暂无持续跟踪基金；可导入持仓，或从价值方向、量化扫描保存研究。</p>
      )}
      {!!groups.size && (
        <div
          className="op-watch-table-scroll"
          tabIndex={0}
          aria-label="基金跟踪表，可横向滚动"
        >
          <table className="op-watch-table" aria-label="持续跟踪与持仓基金">
            <thead>
              <tr>
                <th scope="col">基金 / 来源</th>
                <th scope="col">最新确认净值</th>
                <th scope="col">基金估算净值 / 涨跌幅</th>
                <th scope="col">冻结买卖点</th>
                <th scope="col">当前判断 / 研究</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {[...groups.values()].map((row) => {
                const watches = row.tracks.flatMap((track) =>
                  track.priceWatch ? [track.priceWatch] : [],
                );
                const confirmed = newest([
                  row.holding?.confirmedNav,
                  ...watches.map((watch) => watch.confirmedNav),
                ]);
                const estimate = newest<
                  NonNullable<StudyPriceWatch["currentEstimate"]>
                >([
                  row.holding?.currentEstimate,
                  ...row.tracks.map((track) => track.currentEstimate),
                  ...watches.map(currentFundEstimate),
                ]);
                return (
                  <tr key={row.key}>
                    <th scope="row">
                      <strong>{fundDisplayName(row.label, row.code)}</strong>
                      <span className="op-watch-code">
                        {row.code ?? "代码待核对"}
                      </span>
                      <div className="op-watch-origins">
                        {row.holding && <span>持仓基金</span>}
                        {!!row.tracks.length && <span>手动跟踪</span>}
                      </div>
                    </th>
                    <td>
                      <LiveFundWatchQuote
                        code={row.market === "FUND_CN" ? row.code : undefined}
                        row={confirmed}
                      />
                    </td>
                    <td>
                      <LiveFundWatchQuote
                        code={row.market === "FUND_CN" ? row.code : undefined}
                        row={estimate}
                        estimate
                      />
                    </td>
                    <td>
                      {row.tracks.length ? (
                        row.tracks.map((track) => (
                          <FrozenPlan key={track.id} track={track} />
                        ))
                      ) : (
                        <small>尚未保存买卖计划</small>
                      )}
                    </td>
                    <td>
                      {row.tracks.length ? (
                        row.tracks.map((track) => {
                          const watch = track.priceWatch;
                          const state =
                            watch?.state === "HOLDING" &&
                            watch.holding.state !== "CONFIRMED"
                              ? "UNKNOWN"
                              : watch?.state;
                          return (
                            <div key={track.id} className="op-watch-study">
                              {state && (
                                <strong
                                  className={`op-price-watch-state state-${state.toLowerCase()}`}
                                >
                                  {watchStateLabels[state]}
                                </strong>
                              )}
                              <small>
                                {studyStates[track.status] ?? "状态待核对"}
                                {track.unreadCount
                                  ? ` · ${track.unreadCount}项未读变化`
                                  : ""}
                              </small>
                              {watch?.reason && <small>{watch.reason}</small>}
                              <button
                                type="button"
                                onClick={() => review(track.id)}
                              >
                                查看 {track.title} 与复盘
                              </button>
                            </div>
                          );
                        })
                      ) : (
                        <small>持仓观察；尚无冻结研究计划</small>
                      )}
                    </td>
                    <td>
                      <button
                        type="button"
                        disabled={pending !== null}
                        onClick={() => void cancel(row)}
                        aria-label={`取消跟踪 ${row.code ?? row.label}`}
                      >
                        {pending === row.key ? "取消中…" : "取消跟踪"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

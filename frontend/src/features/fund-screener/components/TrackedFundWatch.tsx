import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getStudyTracks, type StudyTrack } from "../../../api/study-api";
import { fundDisplayName } from "../plan-display";
import { studyStates } from "./study-display";
import PriceWatchSummary from "./PriceWatchSummary";

const shanghaiDate = (time: number) =>
  new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(time));

export default function TrackedFundWatch({ actor }: { actor: string }) {
  const [, setParams] = useSearchParams();
  const [today, setToday] = useState(() => shanghaiDate(Date.now()));
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
  });
  const groups = new Map<string, StudyTrack[]>();
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
    const key = `${track.scope.fundMarket ?? plan?.market ?? ""}:${identity}`;
    const group = groups.get(key) ?? [];
    if (!group.some((item) => item.id === track.id)) group.push(track);
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
  return (
    <section className="op-tracked-funds" aria-label="我的持续跟踪基金">
      <header>
        <h3>我的持续跟踪基金</h3>
        <button
          type="button"
          disabled={list.isFetching}
          onClick={() => void list.refetch()}
        >
          刷新跟踪行情
        </button>
      </header>
      <p>
        持续核对已冻结的买入、止损与止盈价位；今日未再次推荐也会显示。条件触发后请核对证据与持仓。
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
      {list.isSuccess && !groups.size && (
        <p>
          暂无持续跟踪基金；从价值方向或量化扫描保存研究后，在此观察冻结买卖点。
        </p>
      )}
      <div className="op-tracked-fund-grid">
        {[...groups].map(([key, tracks]) => {
          const first = tracks[0];
          const plan = first.priceWatch?.frozenPlan;
          const code =
            first.scope.fundCode ?? plan?.fundCode ?? plan?.subjectId;
          return (
            <article key={key}>
              <h4>
                {fundDisplayName(plan?.label ?? first.title, code ?? "")} {code}
              </h4>
              {tracks.map((track) => (
                <div key={track.id}>
                  <p>
                    {track.title} · {studyStates[track.status] ?? "状态待核对"}
                    {track.unreadCount
                      ? ` · ${track.unreadCount}项未读变化`
                      : ""}
                  </p>
                  <PriceWatchSummary watch={track.priceWatch} />
                  {!track.priceWatch && (
                    <p>行情与冻结价位待核对；打开研究详情确认依据。</p>
                  )}
                  <button type="button" onClick={() => review(track.id)}>
                    查看 {track.title} 与复盘
                  </button>
                </div>
              ))}
            </article>
          );
        })}
      </div>
    </section>
  );
}

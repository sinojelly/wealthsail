import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getStudyTracks } from "../../api/study-api";
import { groupPersonalFunds } from "./personal-fund-model";
import { useStudyQuoteRefresh } from "./use-study-tracking";
import { getPersonalFundsReference } from "../../api/personal-funds";
import { privateQueryKey, useAuthSession } from "../../app/auth-session";

function shanghaiDate() {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

export function usePersonalFunds(actor: string) {
  const session = useAuthSession();
  useStudyQuoteRefresh(actor);
  const [today, setToday] = useState(shanghaiDate);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    function schedule() {
      const day = 86_400_000;
      const offset = 28_800_000;
      const now = Date.now();
      const midnight = (Math.floor((now + offset) / day) + 1) * day - offset;
      timer = setTimeout(() => {
        setToday(shanghaiDate());
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
  const reference = useQuery({
    queryKey: privateQueryKey(
      session,
      "personal-funds-reference",
      actor,
      "research-included",
    ),
    queryFn: ({ signal }) => getPersonalFundsReference(true, signal),
    enabled: !!actor,
    staleTime: 30_000,
  });
  return {
    list,
    reference,
    today,
    groups: groupPersonalFunds(
      list.data ?? { items: [], total: 0 },
      today,
      reference.data?.funds,
    ),
  };
}

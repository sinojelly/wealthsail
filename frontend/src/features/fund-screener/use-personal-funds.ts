import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getStudyTracks } from "../../api/study-api";
import { groupPersonalFunds } from "./personal-fund-model";
import { useStudyQuoteRefresh } from "./use-study-tracking";
import {
  getPersonalFundsReference,
  refreshPersonalFundsReference,
} from "../../api/personal-funds";
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
  const queryClient = useQueryClient();
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
      "personal-only",
    ),
    queryFn: async ({ signal }) => {
      const data = await getPersonalFundsReference(false, signal);
      if (
        Object.values(data.navSync?.states ?? {}).some(
          (row) => row.state === "READY",
        )
      )
        void queryClient.invalidateQueries({
          queryKey: privateQueryKey(session, "opportunity-context"),
        });
      return data;
    },
    enabled: !!actor,
    staleTime: 30_000,
  });
  return {
    list,
    reference,
    today,
    refresh: async () => {
      const data = await refreshPersonalFundsReference();
      queryClient.setQueryData(
        privateQueryKey(
          session,
          "personal-funds-reference",
          actor,
          "personal-only",
        ),
        data,
      );
      await list.refetch();
      void queryClient.invalidateQueries({
        queryKey: privateQueryKey(session, "opportunity-context"),
      });
    },
    groups: groupPersonalFunds(
      list.data ?? { items: [], total: 0 },
      today,
      reference.data?.funds,
    ),
  };
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import * as api from "../../api/study-api";
import { subscribeLatestFundQuoteUpdates } from "./latest-quote-resource";

export function useStudyQuoteRefresh(actor: string) {
  const client = useQueryClient();
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const unsubscribe = subscribeLatestFundQuoteUpdates(() => {
      if (timer !== undefined) return;
      timer = setTimeout(() => {
        timer = undefined;
        // The quote endpoint has filled the public server cache. Re-read the
        // actor's judgment once; never infer trigger states in the browser.
        void client.invalidateQueries(
          { queryKey: ["study", actor] },
          { cancelRefetch: false },
        );
      }, 25);
    });
    return () => {
      unsubscribe();
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [client, actor]);
}
export function useStudyTracking(actor: string, id: string | null, offset = 0) {
  const client = useQueryClient();
  useStudyQuoteRefresh(actor);
  const list = useQuery({
    queryKey: ["study", actor, "list"],
    queryFn: ({ signal }) => api.getStudyTracks(signal),
  });
  const detail = useQuery({
    queryKey: ["study", actor, "detail", id, offset],
    queryFn: ({ signal }) => api.getStudyDetail(id!, signal, offset),
    enabled: !!id,
  });
  const command = useMutation({
    mutationFn: (run: () => Promise<api.StudyDetail>) => run(),
    onSuccess: (result) => {
      client.setQueryData(
        ["study", actor, "detail", result.track.id, 0],
        result,
      );
      void client.invalidateQueries({ queryKey: ["study", actor, "list"] });
      void client.invalidateQueries({
        queryKey: ["study", actor, "detail", result.track.id],
      });
    },
  });
  return { list, detail, command };
}

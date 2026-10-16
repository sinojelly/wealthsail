import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "../../api/study-api";
export function useStudyTracking(actor: string, id: string | null, offset = 0) {
  const client = useQueryClient();
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

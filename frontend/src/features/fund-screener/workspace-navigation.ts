export type ResearchWorkspace = "events" | "stories" | "tracking" | "macro";

export function resolveResearchWorkspace(
  params: URLSearchParams,
): ResearchWorkspace {
  const tab = params.get("researchTab");
  if (
    tab === "events" ||
    tab === "stories" ||
    tab === "tracking" ||
    tab === "macro"
  ) {
    return tab;
  }
  if (tab === "quality" || params.get("researchData") === "1") {
    return "macro";
  }
  return params.get("studyTrack") ? "tracking" : "events";
}

export function workspaceParams(
  params: URLSearchParams,
  workspace: ResearchWorkspace,
): URLSearchParams {
  const next = new URLSearchParams(params);
  next.set("researchTab", workspace);
  next.set("researchOpen", "1");
  next.delete("researchData");
  return next;
}

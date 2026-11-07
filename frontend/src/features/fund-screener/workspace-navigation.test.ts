import {
  resolveResearchWorkspace,
  workspaceParams,
  type ResearchWorkspace,
} from "./workspace-navigation";

describe("resolveResearchWorkspace", () => {
  it.each([
    ["", "events"],
    ["researchTab=events", "events"],
    ["researchTab=stories", "stories"],
    ["researchTab=tracking", "tracking"],
    ["researchTab=macro", "macro"],
    ["researchTab=quality", "macro"],
    ["researchData=1", "macro"],
    ["researchData=0", "events"],
    ["researchTab=unknown", "events"],
  ])("resolves %s to %s", (query, workspace) => {
    expect(resolveResearchWorkspace(new URLSearchParams(query))).toBe(
      workspace,
    );
  });

  it.each([
    ["events", "events"],
    ["stories", "stories"],
    ["tracking", "tracking"],
    ["macro", "macro"],
    ["quality", "macro"],
  ])(
    "keeps explicit %s ahead of retained studyTrack and the legacy data flag",
    (tab, workspace) => {
      const params = new URLSearchParams({
        researchTab: tab,
        studyTrack: "my-original-study",
        researchData: "1",
      });

      expect(resolveResearchWorkspace(params)).toBe(workspace);
    },
  );

  it.each([
    ["studyTrack=my-original-study", "tracking"],
    ["researchTab=unknown&studyTrack=my-original-study", "tracking"],
    ["researchTab=&studyTrack=my-original-study", "tracking"],
    ["researchData=1&studyTrack=my-original-study", "macro"],
    [
      "researchTab=unknown&researchData=1&studyTrack=my-original-study",
      "macro",
    ],
  ])("restores old deep link %s to %s", (query, workspace) => {
    expect(resolveResearchWorkspace(new URLSearchParams(query))).toBe(
      workspace,
    );
  });
});

describe("workspaceParams", () => {
  it.each<ResearchWorkspace>(["events", "stories", "tracking", "macro"])(
    "switches to %s while preserving selected instruments and research identity",
    (workspace) => {
      const params = new URLSearchParams(
        "researchTab=quality&researchTab=unknown&researchData=1&researchData=0&researchOpen=0&fund=510330&index=hs300&direction=a-share%3Abroad&studyTrack=my-original-study&filter=one&filter=two&empty=",
      );
      const original = params.toString();

      const next = workspaceParams(params, workspace);

      expect(next).not.toBe(params);
      expect(next.getAll("researchTab")).toEqual([workspace]);
      expect(next.get("researchOpen")).toBe("1");
      expect(next.has("researchData")).toBe(false);
      expect(next.get("fund")).toBe("510330");
      expect(next.get("index")).toBe("hs300");
      expect(next.get("direction")).toBe("a-share:broad");
      expect(next.get("studyTrack")).toBe("my-original-study");
      expect(next.getAll("filter")).toEqual(["one", "two"]);
      expect(next.get("empty")).toBe("");
      expect(resolveResearchWorkspace(next)).toBe(workspace);
      expect(params.toString()).toBe(original);
    },
  );
});

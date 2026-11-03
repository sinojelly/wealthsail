import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../api/research-acceptance";
import { HttpError } from "../../api/client";
import { renderWithSession } from "../../test/render-with-session";
import ResearchAcceptance from "./components/ResearchAcceptance";
import DailyResearchReport from "./components/DailyResearchReport";
import type { DailyResearch } from "../../api/investment";
import { useQuery } from "@tanstack/react-query";
import * as refreshApi from "../../api/daily-research-debug";
import DailyResearchRefresh from "./components/DailyResearchRefresh";

vi.mock("../../api/research-acceptance", () => ({
  getResearchAcceptance: vi.fn(),
  getResearchArtifacts: vi.fn(),
  getResearchReplays: vi.fn(),
  replayResearchAcceptance: vi.fn(),
}));
vi.mock("../../api/daily-research-debug", () => ({
  getResearchRefresh: vi.fn(),
  startResearchRefresh: vi.fn(),
  getResearchRefreshStatus: vi.fn(),
}));
vi.mock("../../app/auth-session", async (original) => ({
  ...(await original<typeof import("../../app/auth-session")>()),
  useAuthSession: () => ({
    tenantId: "tenant-a",
    userId: "owner-a",
    role: "OWNER",
  }),
}));

const runId = "2626b2df-aabb-4823-9879-6525c7339636";
const outcome = (admin = false): api.ResearchAcceptanceOutcome => ({
  id: "initial-acceptance",
  researchId: runId,
  kind: "INITIAL",
  decision: "PARTIAL",
  validatorVersion: "acceptance-v1",
  validatedAt: "2026-11-03T08:30:00+08:00",
  actionable: false,
  formalPublishable: false,
  issueCount: 2,
  issueListComplete: true,
  canInspectArtifacts: admin,
  canReplay: admin,
  counts: {
    sources: { inputCount: 2, acceptedCount: 1, quarantinedCount: 1 },
    valueDirections: { inputCount: 1, acceptedCount: 1, quarantinedCount: 0 },
  },
  accepted: {
    sources: [
      {
        id: "source:one",
        title: "已核验的行业消息",
        url: "https://example.test/news",
        quote: "来源原文",
      },
      {
        id: "source:unsafe",
        title: "不可用链接",
        url: "javascript:alert(1)",
        quote: "只显示文字",
      },
    ],
    industryCycleReviews: [
      { industry: "NONFERROUS", evidenceSummary: "工业金属观察，仍需核对。" },
    ],
    valueDirections: [
      {
        key: "metals",
        title: "工业金属观察",
        thesis: "等待下游订单核验。",
        watchFor: ["实际回款"],
        sourceIds: ["source:one"],
      },
    ],
    events: [
      {
        id: "event-1",
        title: "行业发布会",
        summary: "已披露经营信息。",
        sourceIds: ["source:one"],
      },
    ],
    upcoming: [
      {
        id: "future-1",
        title: "下一次业绩披露",
        scheduledDate: "2026-11-10",
        watchFor: ["利润兑现"],
        sourceIds: [],
      },
    ],
  },
  issues: [
    {
      issueId: "issue-1",
      researchId: runId,
      itemType: "SOURCE",
      itemIndex: 1,
      path: "sources[1]",
      ruleId: "SOURCE_REFERENCE_INVALID",
      reasonCode: "SOURCE_REFERENCE_INVALID",
      disposition: "QUARANTINED",
      message: "来源引用未通过校验。",
    },
    {
      issueId: "issue-2",
      researchId: runId,
      itemType: "FUND_PLAN",
      itemIndex: 0,
      path: "valueDirections[0].plans[0]",
      ruleId: "DIRECTION_PLAN_SECTOR_UNREVIEWED",
      reasonCode: "FUND_PLAN_INVALID",
      disposition: "BLOCKED",
      message: "基金计划尚未完成行业核查。",
      businessKey: "PRIVATE-BUSINESS-KEY",
      dependencies: ["PRIVATE-DEPENDENCY"],
      missingIndustryCodes: ["NONFERROUS"],
    },
  ],
});

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(outcome());
  vi.mocked(api.getResearchArtifacts).mockResolvedValue({
    researchId: runId,
    complete: true,
    items: [],
  });
  vi.mocked(api.getResearchReplays).mockResolvedValue({ items: [] });
  vi.mocked(refreshApi.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: null,
  });
});

it("shows partial observations and complete safe issues without any trade controls", async () => {
  const data = outcome();
  Object.assign(data.accepted.valueDirections[0], {
    plans: [{ entryZoneMin: "0.0001", fundCode: "004432" }],
  });
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(data);
  renderWithSession(<ResearchAcceptance runId={runId} />);
  expect(
    await screen.findByText("部分验收成果·仅供观察，未正式发布"),
  ).toBeVisible();
  expect(screen.getByText("工业金属观察")).toBeVisible();
  expect(screen.getByText("等待下游订单核验。")).toBeVisible();
  expect(
    screen.getByRole("link", { name: "已核验的行业消息" }),
  ).toHaveAttribute("href", "https://example.test/news");
  expect(
    screen.queryByRole("link", { name: "不可用链接" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("来源引用未通过校验。")).toBeVisible();
  expect(screen.getByText("基金计划尚未完成行业核查。")).toBeVisible();
  expect(screen.queryByText("PRIVATE-BUSINESS-KEY")).not.toBeInTheDocument();
  expect(screen.queryByText("PRIVATE-DEPENDENCY")).not.toBeInTheDocument();
  expect(screen.queryByText("0.0001")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /买入|跟踪|推送|重放|原始/ }),
  ).not.toBeInTheDocument();
  expect(api.getResearchArtifacts).not.toHaveBeenCalled();
  expect(api.getResearchReplays).not.toHaveBeenCalled();
});

it("quietly hides legacy runs with no stored validation outcome", async () => {
  vi.mocked(api.getResearchAcceptance).mockRejectedValue(
    new HttpError("not found", 404),
  );
  const rendered = renderWithSession(<ResearchAcceptance runId={runId} />);
  await waitFor(() => expect(api.getResearchAcceptance).toHaveBeenCalledOnce());
  expect(rendered.container).toBeEmptyDOMElement();
  expect(api.getResearchArtifacts).not.toHaveBeenCalled();
  expect(api.replayResearchAcceptance).not.toHaveBeenCalled();
});

it("keeps a complete initial outcome compact instead of repeating the formal report", async () => {
  vi.mocked(api.getResearchAcceptance).mockResolvedValue({
    ...outcome(),
    decision: "COMPLETE",
    formalPublishable: true,
  });
  renderWithSession(<ResearchAcceptance runId={runId} />);
  expect(await screen.findByText("验收完整")).toBeVisible();
  expect(screen.queryByText("等待下游订单核验。")).not.toBeInTheDocument();
});

it.each(["PARTIAL", "COMPLETE"] as const)(
  "marks excerpted acceptance content visibly even in a compact %s outcome",
  async (decision) => {
    vi.mocked(api.getResearchAcceptance).mockResolvedValue(
      Object.assign(outcome(), { decision, acceptedContentComplete: false }),
    );
    renderWithSession(<ResearchAcceptance runId={runId} />);
    expect(
      await screen.findByText("验收正文为节选，完整内容请管理员查看冻结材料"),
    ).toBeVisible();
    if (decision === "COMPLETE")
      expect(screen.queryByText("等待下游订单核验。")).not.toBeInTheDocument();
    expect(api.getResearchArtifacts).not.toHaveBeenCalled();
    expect(api.replayResearchAcceptance).not.toHaveBeenCalled();
  },
);

it("keeps every issue and filters by category and rule without hiding the total", async () => {
  const data = outcome(true);
  data.issues = Array.from({ length: 43 }, (_, index) => ({
    ...data.issues![index % 2],
    issueId: `issue-${index}`,
    message: `固定问题说明 ${index}`,
  }));
  data.issueCount = 43;
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(data);
  renderWithSession(<ResearchAcceptance runId={runId} />);
  expect(await screen.findByText("固定问题说明 42")).toBeVisible();
  expect(screen.getByText(/共43项/)).toBeVisible();
  await userEvent.selectOptions(screen.getByLabelText("条目类别"), "FUND_PLAN");
  expect(screen.queryByText("固定问题说明 42")).not.toBeInTheDocument();
  expect(screen.getByText("固定问题说明 41")).toBeVisible();
  await userEvent.selectOptions(screen.getByLabelText("条目类别"), "ALL");
  await userEvent.selectOptions(
    screen.getByLabelText("规则代码"),
    "SOURCE_REFERENCE_INVALID",
  );
  expect(screen.getByText("固定问题说明 42")).toBeVisible();
  expect(screen.queryByText("固定问题说明 41")).not.toBeInTheDocument();
});

it("loads complete raw artifacts only when requested and renders them as escaped text", async () => {
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(outcome(true));
  const raw =
    '<img src=x onerror="alert(1)">' + "正文".repeat(5000) + "完整原文末尾";
  vi.mocked(api.getResearchArtifacts).mockResolvedValue({
    researchId: runId,
    complete: true,
    items: [
      {
        kind: "RAW_MODEL",
        content: raw,
        sha256: "a".repeat(64),
        byteLength: raw.length,
      },
    ],
  });
  renderWithSession(<ResearchAcceptance runId={runId} />);
  await screen.findByText("部分验收成果·仅供观察，未正式发布");
  expect(api.getResearchArtifacts).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "查看冻结输入与原始结果" }),
  );
  const original = await screen.findByLabelText("模型原文完整原文");
  expect(original.textContent).toBe(raw);
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
  expect(screen.getByText("PRIVATE-BUSINESS-KEY")).toBeVisible();
  expect(api.getResearchArtifacts).toHaveBeenCalledTimes(1);
});

it("shows structured administrator dependencies as readable item associations", async () => {
  const data = outcome(true);
  Object.assign(data.issues![1], {
    dependencies: [{ itemType: "SOURCE", businessKey: "source:one" }],
  });
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(data);
  renderWithSession(<ResearchAcceptance runId={runId} />);
  expect(await screen.findByText("依赖：来源 · source:one")).toBeVisible();
  expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument();
});

it("shows artifact omissions while keeping replay disabled according to server capability", async () => {
  vi.mocked(api.getResearchAcceptance).mockResolvedValue({
    ...outcome(true),
    canReplay: false,
  });
  vi.mocked(api.getResearchArtifacts).mockResolvedValue({
    researchId: runId,
    complete: false,
    items: [
      {
        kind: "RAW_MODEL",
        content: null,
        omissionReason: "SIZE_LIMIT",
        byteLength: 2097153,
      },
    ],
  });
  renderWithSession(<ResearchAcceptance runId={runId} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "查看冻结输入与原始结果" }),
  );
  expect(await screen.findByText(/SIZE_LIMIT/)).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /离线重放校验/ }),
  ).not.toBeInTheDocument();
});

it("reuses an unacknowledged replay command after timeout and a remount, without changing initial results", async () => {
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(outcome(true));
  vi.mocked(api.replayResearchAcceptance).mockRejectedValueOnce(
    new Error("PRIVATE NETWORK DETAIL"),
  );
  const first = renderWithSession(<ResearchAcceptance runId={runId} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "离线重放校验（不调用Codex）" }),
  );
  await screen.findByRole("button", {
    name: "重试离线重放校验（不调用Codex）",
  });
  const key = vi.mocked(api.replayResearchAcceptance).mock.calls[0][1];
  first.unmount();
  vi.mocked(api.replayResearchAcceptance).mockResolvedValueOnce({
    ...outcome(true),
    id: "replay-1",
    kind: "REPLAY",
    decision: "EMPTY",
    validatorVersion: "acceptance-v2",
    accepted: {
      sources: [],
      industryCycleReviews: [],
      valueDirections: [],
      events: [],
      upcoming: [],
    },
  });
  renderWithSession(<ResearchAcceptance runId={runId} />);
  await userEvent.click(
    await screen.findByRole("button", {
      name: "重试离线重放校验（不调用Codex）",
    }),
  );
  const replay = await screen.findByRole("region", { name: "离线重放结果" });
  expect(within(replay).getByText(/acceptance-v2/)).toBeVisible();
  expect(vi.mocked(api.replayResearchAcceptance).mock.calls[1][1]).toBe(key);
  expect(screen.getByText("部分验收成果·仅供观察，未正式发布")).toBeVisible();
  expect(screen.queryByText("PRIVATE NETWORK DETAIL")).not.toBeInTheDocument();
  expect(api.getResearchReplays).not.toHaveBeenCalled();
});

it("loads saved replays only on explicit request", async () => {
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(outcome(true));
  vi.mocked(api.getResearchReplays).mockResolvedValue({
    items: [
      {
        ...outcome(true),
        id: "saved-replay",
        kind: "REPLAY",
        validatorVersion: "saved-validator-v2",
      },
    ],
  });
  renderWithSession(<ResearchAcceptance runId={runId} />);
  await screen.findByText("部分验收成果·仅供观察，未正式发布");
  expect(api.getResearchReplays).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "查看离线重放记录" }),
  );
  const saved = screen.getByRole("region", { name: "已保存离线重放记录" });
  expect(
    (await within(saved).findAllByText(/saved-validator-v2/))[0],
  ).toBeVisible();
});

it("keeps repaired issues available on a complete outcome without expanding observations", async () => {
  const data = outcome(true);
  data.decision = "COMPLETE";
  data.formalPublishable = true;
  data.issues = [
    {
      ...data.issues![0],
      disposition: "REPAIRED",
      message: "已修复引用格式。",
    },
  ];
  data.issueCount = 1;
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(data);
  renderWithSession(<ResearchAcceptance runId={runId} />);
  await userEvent.click(await screen.findByText("查看验收问题（1项）"));
  expect(screen.getByText("已修复引用格式。")).toBeVisible();
  expect(screen.queryByText("等待下游订单核验。")).not.toBeInTheDocument();
});

it("does not display an outcome bound to another research run", async () => {
  vi.mocked(api.getResearchAcceptance).mockResolvedValue({
    ...outcome(true),
    researchId: "another-run",
  });
  const rendered = renderWithSession(<ResearchAcceptance runId={runId} />);
  await waitFor(() => expect(api.getResearchAcceptance).toHaveBeenCalledOnce());
  expect(rendered.container).toBeEmptyDOMElement();
});

it("adds the current acceptance separately while retaining the earlier published report", async () => {
  const previousRun = "c9dca9af-3e3a-462c-8267-adc18751d46d";
  const daily: DailyResearch = {
    methodVersion: "daily-research-v1",
    state: "FAILED",
    runId,
    reportRunId: previousRun,
    scheduledFor: null,
    startedAt: null,
    completedAt: null,
    asOf: null,
    nextRunAt: null,
    report: {
      summary: "上一份正式日报仍然保留",
      coverage: [],
      sources: [],
      events: [],
      upcoming: [],
      energy: [],
    },
  };
  const before = JSON.stringify(daily);
  renderWithSession(<DailyResearchReport data={daily} />);
  expect(
    await screen.findByText("部分验收成果·仅供观察，未正式发布"),
  ).toBeVisible();
  await userEvent.click(screen.getByText("阅读完整日报"));
  expect(screen.getByText("上一份正式日报仍然保留")).toBeVisible();
  expect(api.getResearchAcceptance).toHaveBeenCalledWith(
    runId,
    expect.any(AbortSignal),
  );
  expect(JSON.stringify(daily)).toBe(before);
});

it.each(["FAILED", "UNCERTAIN"] as const)(
  "reloads only this actor's contexts after a completed %s request to reveal partial acceptance",
  async (terminalStatus) => {
    const previousRun = "c9dca9af-3e3a-462c-8267-adc18751d46d";
    const previous: DailyResearch = {
      methodVersion: "daily-research-v1",
      state: "READY",
      runId: previousRun,
      reportRunId: previousRun,
      scheduledFor: null,
      startedAt: null,
      completedAt: null,
      asOf: null,
      nextRunAt: null,
      report: {
        summary: "上一份正式日报仍然保留",
        coverage: [],
        sources: [],
        events: [],
        upcoming: [],
        energy: [],
      },
    };
    const load = vi
      .fn()
      .mockResolvedValueOnce(previous)
      .mockResolvedValue({ ...previous, runId, state: terminalStatus });
    const market = vi.fn().mockResolvedValue({});
    const analysis = vi.fn().mockResolvedValue({});
    const funds = vi.fn().mockResolvedValue({});
    const studies = vi.fn().mockResolvedValue({});
    const otherActor = vi.fn().mockResolvedValue({});
    const ready = vi.fn();
    const request: refreshApi.DebugResearchRequest = {
      id: "failed-manual-1",
      requestMode: "MANUAL",
      status: "QUEUED",
      requestedAt: new Date().toISOString(),
      startedAt: null,
      completedAt: null,
      batchId: null,
      reportRunId: null,
      errorClass: null,
    };
    vi.mocked(refreshApi.startResearchRefresh).mockResolvedValue(request);
    vi.mocked(refreshApi.getResearchRefreshStatus).mockResolvedValue({
      ...request,
      status: terminalStatus,
      completedAt: new Date().toISOString(),
      reportRunId: runId,
    });
    vi.mocked(api.getResearchAcceptance).mockImplementation(async (id) => {
      if (id === runId) return outcome();
      throw new HttpError("not found", 404);
    });
    function Page() {
      const context = useQuery({
        queryKey: ["owner-a", "opportunity-context"],
        queryFn: load,
        staleTime: Infinity,
      });
      useQuery({
        queryKey: ["owner-a", "market-context"],
        queryFn: market,
        staleTime: Infinity,
      });
      useQuery({
        queryKey: ["owner-a", "analysis"],
        queryFn: analysis,
        staleTime: Infinity,
      });
      useQuery({
        queryKey: ["owner-a", "research-funds"],
        queryFn: funds,
        staleTime: Infinity,
      });
      useQuery({
        queryKey: ["owner-a", "study"],
        queryFn: studies,
        staleTime: Infinity,
      });
      useQuery({
        queryKey: ["another-actor", "opportunity-context"],
        queryFn: otherActor,
        staleTime: Infinity,
      });
      return (
        <>
          <DailyResearchRefresh actor="owner-a" onReady={ready} />
          <DailyResearchReport data={context.data} />
        </>
      );
    }
    renderWithSession(<Page />);
    await screen.findByText("阅读完整日报");
    await userEvent.click(screen.getByRole("button", { name: "手动启动研究" }));
    expect(
      await screen.findByText("部分验收成果·仅供观察，未正式发布"),
    ).toBeVisible();
    await userEvent.click(screen.getByText("阅读完整日报"));
    expect(screen.getByText("上一份正式日报仍然保留")).toBeVisible();
    expect(api.getResearchAcceptance).toHaveBeenCalledWith(
      runId,
      expect.any(AbortSignal),
    );
    expect(load).toHaveBeenCalledTimes(2);
    expect(market).toHaveBeenCalledTimes(2);
    for (const unchanged of [analysis, funds, studies, otherActor])
      expect(unchanged).toHaveBeenCalledTimes(1);
    expect(ready).not.toHaveBeenCalled();
    expect(refreshApi.startResearchRefresh).toHaveBeenCalledOnce();
  },
);

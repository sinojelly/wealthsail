import { act, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
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
  vi.mocked(api.getResearchAcceptance).mockReset().mockResolvedValue(outcome());
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

afterEach(() => vi.useRealTimers());

it.each([
  ["AUDIT_PENDING", "待归档"],
  ["ARCHIVING", "归档恢复中"],
  ["UNRECOVERABLE", "无法自动恢复"],
] as const)(
  "separates archive state %s from business rejection",
  async (archiveState, label) => {
    vi.mocked(api.getResearchAcceptance).mockResolvedValue(
      Object.assign(outcome(true), {
        archiveState,
        archivePending: archiveState !== "UNRECOVERABLE",
        decision: "REJECTED",
        canReplay: false,
        archiveMessage: "验收归档待恢复，不影响已发布严格报告。",
        recoverableMaterials: {
          PACKET: true,
          RAW_MODEL: true,
          SOURCE_CHECKS: false,
        },
      }),
    );
    renderWithSession(<ResearchAcceptance runId={runId} state="READY" />);
    expect(await screen.findByText(`归档状态：${label}`)).toBeVisible();
    expect(
      screen.getByText("验收归档待恢复，不影响已发布严格报告。"),
    ).toBeVisible();
    expect(
      screen.queryByText("本次验收未通过·未正式发布"),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/冻结输入：可恢复/)).toBeVisible();
    expect(screen.getByText(/来源核验凭据：缺失或超限/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "查看冻结输入与原始结果" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /离线重放校验/ }),
    ).not.toBeInTheDocument();
  },
);

it("polls pending archival until it is archived without running research or replay", async () => {
  vi.useFakeTimers();
  vi.mocked(api.getResearchAcceptance)
    .mockResolvedValueOnce(
      Object.assign(outcome(), {
        archiveState: "AUDIT_PENDING",
        archivePending: true,
        decision: "REJECTED",
      }),
    )
    .mockResolvedValueOnce(
      Object.assign(outcome(), {
        archiveState: "ARCHIVING",
        archivePending: true,
        decision: "REJECTED",
      }),
    )
    .mockResolvedValue(
      Object.assign(outcome(), {
        archiveState: "ARCHIVED",
        archivePending: false,
      }),
    );
  renderWithSession(<ResearchAcceptance runId={runId} state="READY" />);
  await vi.waitFor(() =>
    expect(screen.getByText("归档状态：待归档")).toBeVisible(),
  );
  await act(async () => vi.advanceTimersByTimeAsync(15000));
  await vi.waitFor(() =>
    expect(screen.getByText("归档状态：归档恢复中")).toBeVisible(),
  );
  await act(async () => vi.advanceTimersByTimeAsync(15000));
  await vi.waitFor(() =>
    expect(screen.getByText("归档状态：已归档")).toBeVisible(),
  );
  expect(screen.getByText("部分验收成果·仅供观察，未正式发布")).toBeVisible();
  await act(async () => vi.advanceTimersByTimeAsync(60000));
  expect(api.getResearchAcceptance).toHaveBeenCalledTimes(3);
  expect(refreshApi.startResearchRefresh).not.toHaveBeenCalled();
  expect(api.replayResearchAcceptance).not.toHaveBeenCalled();
});

it("bounds pending archive polling and keeps recoverable material details private", async () => {
  vi.useFakeTimers();
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(
    Object.assign(outcome(), {
      archiveState: "AUDIT_PENDING",
      archivePending: true,
      decision: "REJECTED",
      recoverableMaterials: {
        PACKET: true,
        RAW_MODEL: true,
        SOURCE_CHECKS: true,
      },
    }),
  );
  renderWithSession(<ResearchAcceptance runId={runId} state="READY" />);
  await vi.waitFor(() =>
    expect(screen.getByText("归档状态：待归档")).toBeVisible(),
  );
  for (let i = 0; i < 40; i++)
    await act(async () => vi.advanceTimersByTimeAsync(15000));
  expect(api.getResearchAcceptance).toHaveBeenCalledTimes(20);
  expect(
    screen.queryByText(/可恢复材料|冻结输入：可恢复/),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "重新检查归档状态" }),
  ).toBeEnabled();
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(
    Object.assign(outcome(), {
      archiveState: "ARCHIVED",
      archivePending: false,
    }),
  );
  await act(async () =>
    screen.getByRole("button", { name: "重新检查归档状态" }).click(),
  );
  await vi.waitFor(() =>
    expect(screen.getByText("归档状态：已归档")).toBeVisible(),
  );
  expect(api.getResearchAcceptance).toHaveBeenCalledTimes(21);
  await act(async () => vi.advanceTimersByTimeAsync(60000));
  expect(api.getResearchAcceptance).toHaveBeenCalledTimes(21);
  expect(refreshApi.startResearchRefresh).not.toHaveBeenCalled();
  expect(api.replayResearchAcceptance).not.toHaveBeenCalled();
});

it.each([
  ["network interruption", new TypeError("PRIVATE_NETWORK_DETAIL")],
  ["temporary server failure", new HttpError("PRIVATE_SERVER_DETAIL", 503)],
])(
  "recovers from one %s during archive polling with a bounded GET retry",
  async (_label, failure) => {
    vi.useFakeTimers();
    vi.mocked(api.getResearchAcceptance)
      .mockResolvedValueOnce(
        Object.assign(outcome(), {
          archiveState: "AUDIT_PENDING",
          archivePending: true,
        }),
      )
      .mockRejectedValueOnce(failure)
      .mockResolvedValue(
        Object.assign(outcome(), {
          archiveState: "ARCHIVED",
          archivePending: false,
        }),
      );
    renderWithSession(<ResearchAcceptance runId={runId} state="READY" />);
    await vi.waitFor(() =>
      expect(screen.getByText("归档状态：待归档")).toBeVisible(),
    );
    expect(
      screen.getByRole("button", { name: "重新检查归档状态" }),
    ).toBeEnabled();
    await act(async () => vi.advanceTimersByTimeAsync(15000));
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    await vi.waitFor(() =>
      expect(screen.getByText("归档状态：已归档")).toBeVisible(),
    );
    await act(async () => vi.advanceTimersByTimeAsync(60000));
    expect(api.getResearchAcceptance).toHaveBeenCalledTimes(3);
    expect(
      screen.queryByText(/PRIVATE_NETWORK_DETAIL|PRIVATE_SERVER_DETAIL/),
    ).not.toBeInTheDocument();
    expect(refreshApi.startResearchRefresh).not.toHaveBeenCalled();
    expect(api.replayResearchAcceptance).not.toHaveBeenCalled();
  },
);

it("bounds initial archive read retries and lets a manual GET recover after the network returns", async () => {
  vi.useFakeTimers();
  vi.mocked(api.getResearchAcceptance).mockRejectedValue(
    new TypeError("PRIVATE_NETWORK_DETAIL"),
  );
  renderWithSession(<ResearchAcceptance runId={runId} state="READY" />);
  await act(async () => vi.advanceTimersByTimeAsync(10000));
  await vi.waitFor(() =>
    expect(screen.getByText("验收归档状态暂不可读取。")).toBeVisible(),
  );
  expect(api.getResearchAcceptance).toHaveBeenCalledTimes(3);
  await act(async () => vi.advanceTimersByTimeAsync(60000));
  expect(api.getResearchAcceptance).toHaveBeenCalledTimes(3);
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(
    Object.assign(outcome(), {
      archiveState: "ARCHIVED",
      archivePending: false,
    }),
  );
  await act(async () =>
    screen.getByRole("button", { name: "重新检查归档状态" }).click(),
  );
  await vi.waitFor(() =>
    expect(screen.getByText("归档状态：已归档")).toBeVisible(),
  );
  expect(api.getResearchAcceptance).toHaveBeenCalledTimes(4);
  expect(screen.queryByText("PRIVATE_NETWORK_DETAIL")).not.toBeInTheDocument();
  expect(refreshApi.startResearchRefresh).not.toHaveBeenCalled();
  expect(api.replayResearchAcceptance).not.toHaveBeenCalled();
});

it("stops archive polling if its record is no longer available", async () => {
  vi.useFakeTimers();
  vi.mocked(api.getResearchAcceptance)
    .mockResolvedValueOnce(
      Object.assign(outcome(), {
        archiveState: "AUDIT_PENDING",
        archivePending: true,
        decision: "REJECTED",
      }),
    )
    .mockRejectedValue(new HttpError("not found", 404));
  const rendered = renderWithSession(
    <ResearchAcceptance runId={runId} state="READY" />,
  );
  await vi.waitFor(() =>
    expect(screen.getByText("归档状态：待归档")).toBeVisible(),
  );
  await act(async () => vi.advanceTimersByTimeAsync(15000));
  await vi.waitFor(() => expect(rendered.container).toBeEmptyDOMElement());
  await act(async () => vi.advanceTimersByTimeAsync(60000));
  expect(api.getResearchAcceptance).toHaveBeenCalledTimes(2);
});

it.each(["RUNNING", "NOT_RUN", "DISABLED", "MISSED", "UNCERTAIN"] as const)(
  "does not request acceptance before a completed run in state %s",
  async (state) => {
    const rendered = renderWithSession(
      <ResearchAcceptance runId={runId} state={state} />,
    );
    await act(async () => {});
    expect(api.getResearchAcceptance).not.toHaveBeenCalled();
    expect(rendered.container).toBeEmptyDOMElement();
  },
);

it("reads acceptance again when the same pipeline run finishes after an earlier silent 404", async () => {
  vi.mocked(api.getResearchAcceptance)
    .mockRejectedValueOnce(new HttpError("not found", 404))
    .mockResolvedValueOnce(outcome());
  const initial: DailyResearch = {
    methodVersion: "daily-research-v1",
    state: "FAILED",
    runId: "c9dca9af-3e3a-462c-8267-adc18751d46d",
    scheduledFor: null,
    startedAt: null,
    completedAt: null,
    asOf: null,
    nextRunAt: null,
    report: null,
    pipeline: {
      id: "pipeline-a",
      researchRunId: runId,
      state: "FAILED",
      stage: "VERIFYING_OUTPUT",
      finishedAt: null,
    },
  };
  let update!: (value: DailyResearch) => void;
  function Page() {
    const [daily, setDaily] = useState(initial);
    update = setDaily;
    return <DailyResearchReport data={daily} />;
  }
  renderWithSession(<Page />);
  await waitFor(() => expect(api.getResearchAcceptance).toHaveBeenCalledOnce());
  expect(api.getResearchAcceptance).toHaveBeenLastCalledWith(
    runId,
    expect.any(AbortSignal),
  );
  expect(
    screen.queryByRole("region", { name: "研究验收记录" }),
  ).not.toBeInTheDocument();
  await act(async () =>
    update({
      ...initial,
      pipeline: { ...initial.pipeline!, state: "RUNNING" },
    }),
  );
  expect(api.getResearchAcceptance).toHaveBeenCalledOnce();
  await act(async () =>
    update({
      ...initial,
      pipeline: {
        ...initial.pipeline!,
        state: "FAILED",
        finishedAt: "2026-11-03T09:00:00+08:00",
      },
    }),
  );
  expect(
    await screen.findByText("部分验收成果·仅供观察，未正式发布"),
  ).toBeVisible();
  expect(api.getResearchAcceptance).toHaveBeenCalledTimes(2);
  expect(refreshApi.startResearchRefresh).not.toHaveBeenCalled();
  expect(api.replayResearchAcceptance).not.toHaveBeenCalled();
});

it("distinguishes checked and unchecked sources and shows unresolved industry risks without trade controls", async () => {
  const data = outcome();
  data.accepted.sources = [
    { id: "checked", title: "已核对的公告", verification: "SOURCE_CHECKED" },
    {
      id: "unchecked",
      title: "尚未核对的媒体线索",
      verification: "UNVERIFIED",
    },
    {
      id: "unavailable",
      title: "暂不可读取的来源",
      verification: "UNAVAILABLE",
    },
  ];
  Object.assign(data.accepted.industryCycleReviews[0], {
    riskLevel: "CONFIRMED",
    confirmedRiskFlags: ["FINANCING_COOLING"],
    suspectedRiskFlags: ["ORDER_CUTS"],
    persistedRiskFlags: ["PRODUCTION_DELAY"],
    evidenceGaps: ["CURRENT_PROFIT_OR_CASH_EVIDENCE_MISSING"],
  });
  Object.assign(data.accepted.valueDirections[0], {
    industryCycle: {
      stage: "EARLY_COMMERCIALIZATION",
      riskLevel: "SUSPECTED",
      riskFlags: ["FORWARD_PLANS_ONLY"],
      evidenceGaps: ["INDUSTRY_SCOPE_UNVERIFIED:ORDER_CUTS"],
    },
  });
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(data);
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
  const sources = within(
    await screen.findByRole("region", { name: "已验收来源" }),
  );
  expect(sources.getByText("原文已核对")).toBeVisible();
  expect(sources.getByText("原文尚未核验")).toBeVisible();
  expect(sources.getByText("原文暂不可用")).toBeVisible();
  const reviews = within(
    screen.getByRole("region", { name: "已验收行业核查" }),
  );
  expect(reviews.getByText("行业风险：已确认")).toBeVisible();
  expect(
    reviews.getByText(/未解决风险：.*融资降温.*下游砍单.*量产推迟/),
  ).toBeVisible();
  expect(
    reviews.getByText(/证据缺口：.*当期利润增长或真实回款证据不足/),
  ).toBeVisible();
  const directions = within(screen.getByRole("region", { name: "观察方向" }));
  expect(directions.getByText("行业风险：待核实")).toBeVisible();
  expect(
    directions.getByText(/未解决风险：.*仅见远期扩产或框架意向/),
  ).toBeVisible();
  expect(
    directions.getByText(/证据缺口：.*下游砍单证据的行业归属待核实/),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /买入|跟踪|推送/ }),
  ).not.toBeInTheDocument();
});

it("keeps accepted macro observations explicitly unverified and shows the validator revision", async () => {
  const data = outcome();
  Object.assign(data, { validatorRevision: "0123456789abcdef".repeat(4) });
  Object.assign(data.accepted, {
    macroOverview: {
      summary: "宏观需求仍待确认。",
      macroCycle: "信用传导尚需后续数据。",
      verification: "MODEL_UNVERIFIED",
      sourceIds: ["source:one"],
      coverage: [
        {
          topic: "DOMESTIC_GROWTH",
          status: "UNVERIFIED",
          summary: "国内需求证据不足。",
        },
        {
          topic: "ENERGY_SHIPPING",
          status: "NOT_FOUND",
          summary: "本轮未检得能源变化。",
        },
      ],
    },
  });
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(data);
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
  const macro = within(
    await screen.findByRole("region", { name: "宏观总纲·模型研判待核实" }),
  );
  expect(macro.getByText("宏观需求仍待确认。")).toBeVisible();
  expect(macro.getByText("信用传导尚需后续数据。")).toBeVisible();
  expect(macro.getByText(/国内增长与信用.*尚未核验/)).toBeVisible();
  expect(macro.getByText(/能源与航运.*本轮未检得/)).toBeVisible();
  expect(macro.getByText("仅供观察，非可交易建议。")).toBeVisible();
  expect(screen.getByText(/校验修订：0123456789ab/)).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /买入|跟踪|推送/ }),
  ).not.toBeInTheDocument();
});

it.each([40, 64])(
  "shows an available %s-character build commit as a short read-only version",
  async (length) => {
    const buildCommit = "0123456789abcdef".repeat(4).slice(0, length);
    vi.mocked(api.getResearchAcceptance).mockResolvedValue(
      Object.assign(outcome(), { buildCommit }),
    );
    renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
    expect(
      await screen.findByText(
        `构建版本：${buildCommit.slice(0, length === 40 ? 7 : 12)}`,
        { exact: false },
      ),
    ).toBeVisible();
    expect(refreshApi.startResearchRefresh).not.toHaveBeenCalled();
    expect(api.replayResearchAcceptance).not.toHaveBeenCalled();
  },
);

it.each(["PARTIAL", "COMPLETE"] as const)(
  "shows recorded workload compactly for %s without calculating cost or starting work",
  async (decision) => {
    vi.mocked(api.getResearchAcceptance).mockResolvedValue(
      Object.assign(outcome(), {
        decision,
        researchWorkload: {
          version: "research-workload-v1",
          promptBytes: 4096,
          modelElapsedMs: 12345,
          sourceVerificationElapsedMs: 1500,
          inputTokens: 1600,
          cachedInputTokens: 800,
          outputTokens: 200,
          executionCoverage: "COMPLETE",
          observedWebToolCalls: 54,
          observedSearchQueries: 52,
          observedPageOpens: 2,
          observedPageFinds: 0,
          preDispatchEnforcement: false,
          providerRetries: null,
          searchSoftLimitExceeded: true,
        },
      }),
    );
    renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
    await userEvent.click(await screen.findByText("研究工作量（只读观测）"));
    const workload = within(screen.getByLabelText("研究工作量"));
    expect(workload.getByText(/Prompt：4096字节/)).toBeVisible();
    expect(workload.getByText(/模型阶段耗时：12.35秒/)).toBeVisible();
    expect(workload.getByText(/来源核验耗时：1.50秒/)).toBeVisible();
    expect(
      workload.getByText(/输入token：1600.*缓存输入token：800.*输出token：200/),
    ).toBeVisible();
    expect(workload.getByText(/执行观测覆盖：完整观测/)).toBeVisible();
    expect(
      workload.getByText(
        /Web工具调用：54.*搜索查询：52.*页面打开：2.*页面查找：0/,
      ),
    ).toBeVisible();
    expect(
      workload.getByText("观测超过计划参考线，未具备请求前硬拦截"),
    ).toBeVisible();
    expect(workload.getByText(/供应商重试：未知/)).toBeVisible();
    expect(workload.queryByText(/%|￥|\$\d/)).not.toBeInTheDocument();
    expect(api.replayResearchAcceptance).not.toHaveBeenCalled();
    expect(refreshApi.startResearchRefresh).not.toHaveBeenCalled();
  },
);

it("keeps unavailable workload observations unknown rather than inventing zeroes or tokens", async () => {
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(
    Object.assign(outcome(), {
      researchWorkload: {
        version: "research-workload-v1",
        executionCoverage: "PARTIAL",
        observedWebToolCalls: null,
        observedSearchQueries: null,
        observedPageOpens: null,
        observedPageFinds: null,
        preDispatchEnforcement: false,
        providerRetries: null,
        searchSoftLimitExceeded: null,
      },
    }),
  );
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
  await userEvent.click(await screen.findByText("研究工作量（只读观测）"));
  const workload = within(screen.getByLabelText("研究工作量"));
  expect(
    workload.getByText(/Prompt：未知.*模型阶段耗时：未知.*来源核验耗时：未知/),
  ).toBeVisible();
  expect(
    workload.getByText(/输入token：未知.*缓存输入token：未知.*输出token：未知/),
  ).toBeVisible();
  expect(
    workload.getByText(
      /Web工具调用：未知.*搜索查询：未知.*页面打开：未知.*页面查找：未知/,
    ),
  ).toBeVisible();
  expect(workload.getByText(/执行观测覆盖：部分观测/)).toBeVisible();
  expect(workload.queryByText(/：0/)).not.toBeInTheDocument();
  expect(
    workload.queryByText("观测超过计划参考线，未具备请求前硬拦截"),
  ).not.toBeInTheDocument();
});

it("does not add fabricated workload observations to old outcomes without metadata", async () => {
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
  await screen.findByText("部分验收成果·仅供观察，未正式发布");
  expect(screen.queryByText("研究工作量（只读观测）")).not.toBeInTheDocument();
  expect(screen.queryByText(/输入token|Prompt：/)).not.toBeInTheDocument();
});

it("shows partial observations and complete safe issues without any trade controls", async () => {
  const data = outcome();
  Object.assign(data.accepted.valueDirections[0], {
    plans: [{ entryZoneMin: "0.0001", fundCode: "004432" }],
  });
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(data);
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
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

it("preserves a source-backed negative event and namespace gaps without needing any formal direction", async () => {
  const data = outcome();
  data.accepted.valueDirections = [];
  Object.assign(data.accepted.sources[0], {
    verification: "SOURCE_CHECKED",
    publishedDate: "2026-11-03",
    retrievedAt: "2026-11-03T08:30:00+08:00",
  });
  Object.assign(data.accepted.events[0], {
    phase: "REPORTED",
    importance: "HIGH",
    sourceIds: ["source:one", "source:unsafe", "missing-source"],
    channels: ["融资约束可能拖慢供应链回款。"],
    scenarios: [
      {
        condition: "融资未恢复",
        impact: "交付可能延期",
        counter: "新增订单回款改善",
      },
    ],
    unknowns: ["行业风险是否扩散尚未确认。"],
    relatedTopicKeys: ["dynamic-topic"],
    relatedFormalDirectionKeys: [],
    relatedMacroConcepts: ["US_RATES"],
    candidateConceptLabels: ["新材料候选概念"],
    referenceResolution: {
      version: "research-event-links-v1",
      resolved: [
        {
          namespace: "TOPIC",
          inputKey: "Dynamic Topic",
          key: "dynamic-topic",
          method: "CANONICAL",
        },
      ],
      unresolved: [
        {
          namespace: "LEGACY_DIRECTION",
          key: "unknown-direction",
          reasonCode: "UNKNOWN_REFERENCE",
        },
      ],
    },
  });
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(data);
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
  const events = within(
    await screen.findByRole("region", { name: "已验收事件" }),
  );
  expect(events.getByText(/重要性：高/)).toBeVisible();
  expect(events.getByText(/阶段：报道线索/)).toBeVisible();
  expect(events.getByText("融资约束可能拖慢供应链回款。")).toBeVisible();
  for (const text of [
    "融资未恢复",
    "交付可能延期",
    "新增订单回款改善",
    "行业风险是否扩散尚未确认。",
  ])
    expect(events.getByText(text, { exact: false })).toBeVisible();
  expect(events.getByText(/关联观察主题：dynamic-topic/)).toBeVisible();
  expect(events.getByText(/宏观概念声明.*US_RATES/)).toBeVisible();
  expect(events.getByText(/候选概念声明.*新材料候选概念/)).toBeVisible();
  expect(
    events.getByText(/未解决关联.*unknown-direction.*未找到匹配引用/),
  ).toBeVisible();
  await userEvent.click(events.getByText("查看本项来源依据"));
  expect(
    events.getByRole("link", { name: "已核验的行业消息" }),
  ).toHaveAttribute("href", "https://example.test/news");
  expect(events.getByText("原文已核对")).toBeVisible();
  expect(events.getByText(/公布：2026年11月3日/)).toBeVisible();
  expect(
    events.queryByRole("link", { name: "不可用链接" }),
  ).not.toBeInTheDocument();
  expect(events.getByText(/引用待核查：missing-source/)).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /买入|跟踪|研究|推送/ }),
  ).not.toBeInTheDocument();
  expect(api.replayResearchAcceptance).not.toHaveBeenCalled();
});

it("keeps the current accepted calendar independently read-only with item-specific source evidence", async () => {
  const data = outcome();
  data.accepted.valueDirections = [];
  Object.assign(data.accepted.sources[0], { verification: "UNVERIFIED" });
  Object.assign(data.accepted.upcoming[0], {
    phase: "PLANNED",
    sourceIds: ["source:one"],
    relatedTopicKeys: ["dynamic-topic"],
  });
  vi.mocked(api.getResearchAcceptance).mockResolvedValue(data);
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
  const calendar = within(
    await screen.findByRole("region", { name: "已验收日程" }),
  );
  await userEvent.click(calendar.getByText("本次已验收日程·仅供观察"));
  expect(calendar.getByText("下一次业绩披露")).toBeVisible();
  expect(calendar.getByText(/2026年11月10日.*计划，尚未发生/)).toBeVisible();
  expect(
    calendar.getByText(/实际利润与订单回款仍需后续核对|利润兑现/),
  ).toBeVisible();
  await userEvent.click(calendar.getByText("查看本项来源依据"));
  expect(
    calendar.getByRole("link", { name: "已核验的行业消息" }),
  ).toHaveAttribute("href", "https://example.test/news");
  expect(calendar.getByText("原文尚未核验")).toBeVisible();
  expect(calendar.queryByRole("button")).not.toBeInTheDocument();
  expect(refreshApi.startResearchRefresh).not.toHaveBeenCalled();
});

it("quietly hides legacy runs with no stored validation outcome", async () => {
  vi.mocked(api.getResearchAcceptance).mockRejectedValue(
    new HttpError("not found", 404),
  );
  const rendered = renderWithSession(
    <ResearchAcceptance runId={runId} state="FAILED" />,
  );
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
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
  expect(await screen.findByText("验收完整")).toBeVisible();
  expect(screen.queryByText("等待下游订单核验。")).not.toBeInTheDocument();
});

it.each(["PARTIAL", "COMPLETE"] as const)(
  "marks excerpted acceptance content visibly even in a compact %s outcome",
  async (decision) => {
    vi.mocked(api.getResearchAcceptance).mockResolvedValue(
      Object.assign(outcome(), { decision, acceptedContentComplete: false }),
    );
    renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
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
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
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
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
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
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
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
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
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
  const first = renderWithSession(
    <ResearchAcceptance runId={runId} state="FAILED" />,
  );
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
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
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
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
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
  renderWithSession(<ResearchAcceptance runId={runId} state="FAILED" />);
  await userEvent.click(await screen.findByText("查看验收问题（1项）"));
  expect(screen.getByText("已修复引用格式。")).toBeVisible();
  expect(screen.queryByText("等待下游订单核验。")).not.toBeInTheDocument();
});

it("does not display an outcome bound to another research run", async () => {
  vi.mocked(api.getResearchAcceptance).mockResolvedValue({
    ...outcome(true),
    researchId: "another-run",
  });
  const rendered = renderWithSession(
    <ResearchAcceptance runId={runId} state="FAILED" />,
  );
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
      .mockResolvedValue({
        ...previous,
        runId,
        state: terminalStatus,
        completedAt: new Date().toISOString(),
      });
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

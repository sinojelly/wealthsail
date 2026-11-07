import { act, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { renderWithSession } from "../../test/render-with-session";
import * as api from "../../api/research-topic-registry";
import { HttpError } from "../../api/client";
import ResearchTopicRegistry from "./components/ResearchTopicRegistry";
import DailyResearchReport from "./components/DailyResearchReport";
import MacroContextStrip from "./components/MacroContextStrip";
import type { DailyResearch, OpportunityContext } from "../../api/investment";
import { getResearchAcceptance } from "../../api/research-acceptance";
import { listHistoricalRepairs } from "../../api/history-repairs";
import serverCounts72 from "../../test/research-topic-registry-72-counts.json";
const session = vi.hoisted(() => ({
  tenantId: "tenant-a",
  userId: "reader-a",
  role: "MEMBER",
}));
vi.mock("../../app/auth-session", async (original) => ({
  ...(await original<typeof import("../../app/auth-session")>()),
  useAuthSession: () => session,
}));
vi.mock("../../api/research-topic-registry", () => ({
  getResearchTopicRegistry: vi.fn(),
}));
vi.mock("../../api/research-acceptance", () => ({
  getResearchAcceptance: vi
    .fn()
    .mockRejectedValue(new HttpError("missing", 404)),
}));
vi.mock("../../api/history-repairs", () => ({
  listHistoricalRepairs: vi.fn().mockResolvedValue({ items: [] }),
}));
const run = "ecdf9dce-54e4-4b26-98ab-e1af6f4e1ffb",
  old = "abbc3057-104b-4546-a6b2-ed560f6049f7";
const row = (index = 0): api.RegistryTopic => ({
  topicId: `topic-${index}`,
  key: `key-${index}`,
  title: `动态主题${index}`,
  summary: "只读研究观察摘要",
  status: "PENDING_REVIEW",
  reasonCode: "AUDIT_BUDGET_PENDING",
  reason: "本批审核预算已用尽，等待后续补证。",
  impact: "相关盈利传导尚未得到验证。",
  measures: ["保留观察，未放行正式计划。"],
  pendingTasks: [
    {
      taskId: `task-${index}`,
      industryId: "NEW_SCOPE",
      kind: "FINANCING_COOLING",
      query: "核查融资与订单回款",
      status: "PENDING",
    },
  ],
  sourceIds: ["s1"],
  match: {
    status: "NEW_TOPIC",
    industryIds: [],
    relatedIndustryIds: ["NONFERROUS"],
    evidence: [],
    registryVersion: "registry-v2",
  },
  investmentEligible: false,
});
const page = (researchId = run): api.TopicRegistryPage => ({
  researchId,
  snapshotId: `snapshot-${researchId}`,
  validatedAt: "2026-11-04T08:00:00+08:00",
  revision: "a".repeat(64),
  registryAvailable: true,
  actionable: false,
  items: [row()],
  totalCount: 48,
  nextCursor: null,
  coverage: {
    totalCount: 48,
    plannedScopeCount: 12,
    reviewedScopeCount: 10,
    pendingScopeCount: 2,
    searchExecutionVerified: false,
    maxModelCalls: 1,
    additionalModelCalls: 0,
    maxTopics: 64,
    maxFormalDirections: 8,
    maxScopes: 10,
    maxQueries: 50,
    maxSources: 24,
  },
});
beforeEach(() => {
  vi.resetAllMocks();
  session.tenantId = "tenant-a";
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(page());
  vi.mocked(getResearchAcceptance).mockRejectedValue(
    new HttpError("missing", 404),
  );
  vi.mocked(listHistoricalRepairs).mockResolvedValue({ items: [] });
});
afterEach(() => vi.useRealTimers());
it("highlights server-selected topics beyond page one and retains original paged catalog", async () => {
  const data = page();
  data.totalCount = 64;
  data.items = [row(0)];
  Object.assign(data, {
    highlights: {
      version: "topic-display-priority-v1",
      limit: 10,
      catalogCount: 64,
      actionable: false,
      items: [
        {
          topic: row(63),
          priority: {
            importance: "HIGH",
            checkedSourceCoveragePercent: 50,
            scopeVerified: false,
            researchProgress: "PENDING_REVIEW",
            reasons: ["EXPLICIT_HIGH_IMPORTANCE", "CHECKED_SOURCE_SUPPORT"],
          },
        },
      ],
    },
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(
    <ResearchTopicRegistry runId={run} state="FAILED" variant="HIGHLIGHT" />,
  );
  const highlights = await screen.findByRole("region", {
    name: "重点主题 · 只读优先级",
  });
  expect(within(highlights).getAllByText("动态主题63")[0]).toBeVisible();
  expect(within(highlights).getByText(/高重要性事件关联/)).toBeVisible();
  expect(within(highlights).getByText(/引文核对覆盖50%/)).toBeVisible();
  expect(within(highlights).queryByText("动态主题0")).not.toBeInTheDocument();
  await userEvent.click(screen.getByText("完整主题目录 · 保留原始顺序与分页"));
  expect(screen.getByText("动态主题0")).toBeVisible();
  expect(api.getResearchTopicRegistry).toHaveBeenCalledWith(
    run,
    { limit: 12, cursor: undefined, includeHighlights: true },
    expect.any(AbortSignal),
  );
  expect(
    screen.queryByRole("button", { name: /买入|跟踪/ }),
  ).not.toBeInTheDocument();
});

it("legacy highlight response does not pretend the first ordinal page is priority-ranked", async () => {
  renderWithSession(
    <ResearchTopicRegistry runId={run} state="READY" variant="HIGHLIGHT" />,
  );
  expect(
    await screen.findByText("本次未提供重点排序，完整目录仍可阅读。"),
  ).toBeVisible();
  expect(
    screen.queryByRole("region", { name: "重点主题 · 只读优先级" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("完整主题目录 · 保留原始顺序与分页")).toBeVisible();
  expect(screen.getByText("动态主题0")).toBeVisible();
});
it("shows read-only topic status, match scope, budget gaps, source clock and explanatory tasks without investment actions", async () => {
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(await screen.findByText("动态主题0")).toBeVisible();
  for (const text of [
    "本批审核预算已用尽，等待后续补证。",
    "相关盈利传导尚未得到验证。",
    "保留观察，未放行正式计划。",
    "核查融资与订单回款",
    "s1",
  ])
    expect(screen.getByText(text, { exact: false })).toBeVisible();
  expect(screen.getByText(/审核结果记录10/)).toBeVisible();
  expect(screen.getByText(/待审2/)).toBeVisible();
  expect(screen.getByText(/独立搜索执行凭证尚未验证/)).toBeVisible();
  expect(screen.getByText(/新主题/)).toBeVisible();
  expect(screen.getByText(/仅相关行业/)).toBeVisible();
  expect(screen.getByText(/2026年11月4日 08:00/)).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /跟踪|买入|执行研究|推送/ }),
  ).not.toBeInTheDocument();
  expect(api.getResearchTopicRegistry).toHaveBeenCalledOnce();
});
it("keeps new-insight unknowns and follow-up visible while labeling quote coverage independently of reasoning and investment", async () => {
  const data = page();
  Object.assign(data.items[0], {
    unknowns: ["量产交付是否延期仍待核对。", "当前利润率尚无财报支持。"],
    followUp: ["核对下一期订单回款与实际交付。"],
    insightCategory: "DYNAMIC",
    evidenceCoverageScore: 67,
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  const first = renderWithSession(
    <ResearchTopicRegistry runId={run} state="FAILED" />,
  );
  expect(await screen.findByText("量产交付是否延期仍待核对。")).toBeVisible();
  expect(screen.getByText("当前利润率尚无财报支持。")).toBeVisible();
  expect(screen.getByText("核对下一期订单回款与实际交付。")).toBeVisible();
  expect(
    screen.getByText("引文核对覆盖67%（不代表推理已证实或投资评分）"),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /买入|跟踪|推送|执行研究/ }),
  ).not.toBeInTheDocument();
  first.unmount();
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(page());
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  await screen.findByText("动态主题0");
  expect(screen.queryByText(/引文核对覆盖0%/)).not.toBeInTheDocument();
});
it("supports 12/20/30 pages across a registry larger than eight with opaque cursors and no extra model calls", async () => {
  vi.mocked(api.getResearchTopicRegistry).mockImplementation(
    async (_run, options) => ({
      ...page(),
      items: Array.from({ length: options.limit }, (_, i) =>
        row(i + (options.cursor ? options.limit : 0)),
      ),
      nextCursor: options.cursor ? null : `cursor:${options.limit}`,
    }),
  );
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  await screen.findByText("动态主题11");
  expect(screen.queryByText("动态主题12")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "下一页主题" }));
  expect(await screen.findByText("动态主题12")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "上一页主题" }));
  expect(await screen.findByText("动态主题0")).toBeVisible();
  for (const limit of [20, 30]) {
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "每页主题数" }),
      String(limit),
    );
    expect(await screen.findByText(`动态主题${limit - 1}`)).toBeVisible();
  }
  expect(api.getResearchTopicRegistry).toHaveBeenCalledWith(
    run,
    { limit: 30, cursor: undefined },
    expect.any(AbortSignal),
  );
  expect(screen.getByText(/正式方向每批最多8/)).toBeVisible();
});
it("does not turn composite matching or formal eligibility into live trading permission", async () => {
  const data = page();
  data.items = [
    {
      ...row(),
      status: "FORMAL",
      investmentEligible: true,
      match: { ...row().match, status: "COMPOSITE" },
    },
  ];
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(await screen.findByText(/复合主题/)).toBeVisible();
  expect(screen.getAllByText(/不等于实时可交易/)).toHaveLength(2);
  expect(
    screen.queryByRole("button", { name: /跟踪|买入/ }),
  ).not.toBeInTheDocument();
});
it("keeps observations available when a valid registry explicitly lacks its revision metadata", async () => {
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue({
    ...page(),
    ...{ revision: null, revisionStatus: "UNAVAILABLE" as const },
  });
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(await screen.findByText("动态主题0")).toBeVisible();
  expect(screen.getByText("校验修订信息未提供")).toBeVisible();
  expect(
    screen.queryByText(/修订：null|修订：unknown/),
  ).not.toBeInTheDocument();
});
it("does not turn an invalid blank revision into valid unavailable metadata", async () => {
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue({
    ...page(),
    revision: "",
    ...{ revisionStatus: "UNAVAILABLE" as const },
  });
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(
    await screen.findByText("主题目录结构不完整，暂不展示内容。"),
  ).toBeVisible();
  expect(screen.queryByText("动态主题0")).not.toBeInTheDocument();
  expect(screen.queryByText("校验修订信息未提供")).not.toBeInTheDocument();
});
it("labels deferred planning as unfinished review rather than executing its tasks", async () => {
  const data = page();
  data.items[0].pendingTasks[0].status = "PLANNED_DEFERRED";
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(await screen.findByText(/计划已延后，尚未执行核查/)).toBeVisible();
  expect(screen.getByText("核查融资与订单回款")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /执行|研究|补证/ }),
  ).not.toBeInTheDocument();
});
it("displays model-call budgets and zero additional calls without inventing actual usage", async () => {
  const data = page();
  Object.assign(data.coverage, {
    maxModelCalls: 1,
    additionalModelCalls: 0,
    modelCalls: 999,
    maxInputBytes: 4194304,
    maxOutputBytes: 2097152,
    maxExecutionSeconds: 1200,
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(await screen.findByText(/模型调用预算上限：1次/)).toBeVisible();
  expect(screen.getByText(/目录读取额外模型调用：0次/)).toBeVisible();
  expect(screen.getByText(/实际用量以用量账本为准/)).toBeVisible();
  expect(
    screen.queryByText(/模型调用次数（记录值）|999/),
  ).not.toBeInTheDocument();
});
it("does not describe an accepted strict candidate as published when the batch withheld investment eligibility", async () => {
  const data = page();
  data.items[0].status = "FORMAL";
  data.items[0].investmentEligible = false;
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(
    await screen.findByText(/严格候选已验收，未获得本批发布许可/),
  ).toBeVisible();
  expect(screen.queryByText(/正式链已验收/)).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /跟踪|买入/ }),
  ).not.toBeInTheDocument();
});
it("separates structural completeness from independently executed and evidence-verified checks", async () => {
  const data = page();
  Object.assign(data.coverage, {
    structureCompleteScopeCount: 10,
    executionVerifiedScopeCount: 0,
    evidenceVerifiedCheckCount: 0,
    executionVerifiedCheckCount: 0,
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(await screen.findByText(/结构齐全范围：10/)).toBeVisible();
  expect(screen.getByText(/执行凭证已核验范围：0/)).toBeVisible();
  expect(screen.getByText(/执行凭证已核验检查：0/)).toBeVisible();
  expect(screen.getByText(/证据已核验检查：0/)).toBeVisible();
  expect(screen.getByText(/结构齐全不等于执行或证据核验/)).toBeVisible();
  expect(screen.queryByText(/实际审核10/)).not.toBeInTheDocument();
});
it("separates discovered topics with checked sources from evaluable and formally qualified fund directions", async () => {
  const data = page();
  Object.assign(data.coverage, {
    discoveredTopicCount: 12,
    evidenceBackedTopicCount: 4,
    evaluableFundDirectionCount: 2,
    qualifiedFormalDirectionCount: 0,
    validEventCount: 3,
    validUpcomingCount: 1,
    unresolvedReferenceCount: 2,
    formalEligibilityReasons: {},
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="FAILED" />);
  expect(
    await screen.findByText(/发现主题：12.*具有已核对来源的主题：4/),
  ).toBeVisible();
  expect(
    screen.getByText(/可评估基金方向：2.*符合正式资格方向：0/),
  ).toBeVisible();
  expect(
    screen.getByText(/有效事件：3.*有效日程：1.*未解决引用：2/),
  ).toBeVisible();
  expect(
    screen.getByText(/本次无符合正式资格方向，观察新闻与动态主题仍独立保留/),
  ).toBeVisible();
  expect(screen.getByText("动态主题0")).toBeVisible();
});
it("distinguishes independently validated read-only delivery from the unchanged failed investment report", async () => {
  const data = page();
  Object.assign(data.coverage, {
    researchDeliveryStatus: "VALIDATED_READ_ONLY",
    investmentQualificationStatus: "NO_QUALIFIED_OPPORTUNITIES",
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="FAILED" />);
  expect(
    await screen.findByText(
      "今日经济研究已有可阅读成果，主题核验覆盖尚未提供；暂无合格机会",
    ),
  ).toBeVisible();
  expect(
    screen.getByText("仅表示只读内容通过独立验收，经济传导仍为条件推演"),
  ).toBeVisible();
  expect(screen.getByText(/原投资报告状态保持不变/)).toBeVisible();
  expect(data.coverage.researchDeliveryStatus).toBe("VALIDATED_READ_ONLY");
  expect(
    screen.queryByRole("button", { name: /买入|跟踪|推送|重跑/ }),
  ).not.toBeInTheDocument();
});
it("keeps unverified saved observations and legacy missing delivery states distinct from completed research", async () => {
  const data = page();
  Object.assign(data.coverage, {
    researchDeliveryStatus: "OBSERVATION_READ_ONLY",
    investmentQualificationStatus: "BLOCKED",
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  const first = renderWithSession(
    <ResearchTopicRegistry runId={run} state="FAILED" />,
  );
  expect(
    await screen.findByText("研究观察资料已保存，证据待核实"),
  ).toBeVisible();
  expect(screen.queryByText(/经济研究已完成/)).not.toBeInTheDocument();
  first.unmount();
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(page());
  renderWithSession(<ResearchTopicRegistry runId={run} state="FAILED" />);
  expect(
    await screen.findByText(/只读研究交付状态：旧版未提供，不可推断/),
  ).toBeVisible();
  expect(screen.getByText(/投资资格状态：旧版未提供，不可推断/)).toBeVisible();
  expect(screen.queryByText(/经济研究已完成/)).not.toBeInTheDocument();
});
it("does not turn missing discovery and formal qualification counters into zero or reliable evidence", async () => {
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(
    await screen.findByText(/发现主题：旧版未提供，不可推断/),
  ).toBeVisible();
  expect(
    screen.getByText(/具有已核对来源的主题：旧版未提供，不可推断/),
  ).toBeVisible();
  expect(
    screen.getByText(/符合正式资格方向：旧版未提供，不可推断/),
  ).toBeVisible();
  expect(
    screen.queryByText(/符合正式资格方向：0|具有已核对来源的主题：0/),
  ).not.toBeInTheDocument();
});
it("shows only fixed non-exclusive qualification reasons without guessing unknown codes", async () => {
  const data = page();
  Object.assign(data.coverage, {
    formalEligibilityReasons: {
      NO_FORMAL_PROPOSAL: 1,
      SOURCE_EVIDENCE_PENDING: 2,
      INDUSTRY_AUDIT_PENDING: 1,
      UNSAFE_UNRECOGNIZED_TEXT: 9,
    },
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="FAILED" />);
  expect(await screen.findByText(/未输出正式方向：1/)).toBeVisible();
  expect(screen.getByText(/来源证据待核实：2/)).toBeVisible();
  expect(screen.getByText(/行业风险审核待完成：1/)).toBeVisible();
  expect(
    screen.getByText(/原因计数可重叠，不可相加推算方向总数/),
  ).toBeVisible();
  expect(
    screen.queryByText(/UNSAFE_UNRECOGNIZED_TEXT/),
  ).not.toBeInTheDocument();
});
it("explains that partial CLI streams may still verify individual tasks without implying unknown actions are safe", async () => {
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(
    await screen.findByText(/部分流可有独立任务核验，未知动作不代表无风险/),
  ).toBeVisible();
});
it("shows verified CLI execution events without claiming exhaustive or risk-free research", async () => {
  const data = page();
  Object.assign(data.coverage, { searchExecutionVerified: true });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(await screen.findByText(/CLI执行事件已核验/)).toBeVisible();
  expect(screen.getByText(/不代表检索穷尽或不存在风险/)).toBeVisible();
  expect(
    screen.queryByText(/独立搜索执行凭证尚未验证/),
  ).not.toBeInTheDocument();
  expect(api.getResearchTopicRegistry).toHaveBeenCalledOnce();
});
it("reports partial task matching and partial source coverage without calling the entire study complete", async () => {
  const data = page();
  Object.assign(data.coverage, {
    researchDeliveryStatus: "VALIDATED_READ_ONLY",
    investmentQualificationStatus: "NO_QUALIFIED_OPPORTUNITIES",
    discoveredTopicCount: 64,
    evidenceBackedTopicCount: 2,
    pendingVerificationTopicCount: 62,
    evidenceBackedTopicRatioPercent: 3.1,
    qualifiedFormalDirectionCount: 0,
    executionMatchStatus: "EXECUTION_PARTIALLY_MATCHED",
    executionMatchedTaskCount: 48,
    executionPlannedTaskCount: 50,
    executionUnmatchedQueryHashCount: 0,
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="FAILED" />);
  expect(
    await screen.findByText(
      "今日经济研究已有可阅读成果：64个发现主题，其中2个具备已核对来源支持，其余62个仍需核实；暂无合格机会",
    ),
  ).toBeVisible();
  expect(screen.getByText(/执行任务匹配：计划任务仅部分匹配/)).toBeVisible();
  expect(screen.getByText(/可信匹配任务：48.*计划任务：50/)).toBeVisible();
  expect(
    screen.getByText(/待核实主题：62.*已核对来源支持比例：3.1%/),
  ).toBeVisible();
  expect(screen.queryByText(/经济研究已完成/)).not.toBeInTheDocument();
});
it("keeps explicitly unverified execution distinct from a legacy missing field", async () => {
  const first = renderWithSession(
    <ResearchTopicRegistry runId={run} state="READY" />,
  );
  expect(await screen.findByText(/独立搜索执行凭证尚未验证/)).toBeVisible();
  expect(screen.queryByText(/CLI执行事件已核验/)).not.toBeInTheDocument();
  first.unmount();
  const legacy = page();
  Reflect.deleteProperty(legacy.coverage, "searchExecutionVerified");
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(legacy);
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(
    await screen.findByText(/搜索执行核验状态：旧版未提供，不可推断/),
  ).toBeVisible();
  expect(
    screen.queryByText(/独立搜索执行凭证尚未验证/),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(/CLI执行事件已核验/)).not.toBeInTheDocument();
  expect(screen.queryByText(/执行凭证已核验检查：0/)).not.toBeInTheDocument();
});
it("reports trusted task matching and unattributed queries without claiming an industry was not searched", async () => {
  const data = page();
  Object.assign(data.coverage, {
    executionMatchStatus: "EXECUTION_UNMATCHED",
    executionPlannedTaskCount: 15,
    executionMatchedTaskCount: 3,
    executionObservedQueryCount: 9,
    executionUnmatchedQueryHashCount: 6,
    executionPendingDirectionCount: 2,
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="FAILED" />);
  expect(
    await screen.findByText(/执行任务匹配：存在未归属计划任务的观测查询/),
  ).toBeVisible();
  expect(screen.getByText(/可信匹配任务：3.*计划任务：15/)).toBeVisible();
  expect(screen.getByText(/观测查询：9.*未匹配查询哈希：6/)).toBeVisible();
  expect(screen.getByText(/执行凭证缺口待审方向：2/)).toBeVisible();
  expect(
    screen.getByText(/未匹配查询可能包含宏观等检索，不能自动归入某个行业/),
  ).toBeVisible();
  expect(screen.getByText(/不能据此判断该行业未搜索/)).toBeVisible();
  expect(api.getResearchTopicRegistry).toHaveBeenCalledOnce();
  expect(
    screen.queryByRole("button", { name: /执行研究|买入|跟踪|重跑/ }),
  ).not.toBeInTheDocument();
});
it.each([
  ["EXECUTION_MATCHED", "全部计划任务已匹配"],
  ["EXECUTION_NOT_OBSERVED", "尚未观测到执行事件"],
  ["EXECUTION_UNVERIFIED", "执行凭证尚未核验"],
])(
  "labels saved matching status %s without inferring missing task counts",
  async (executionMatchStatus, label) => {
    const data = page();
    Object.assign(data.coverage, { executionMatchStatus });
    vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
    renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
    expect(await screen.findByText(`执行任务匹配：${label}`)).toBeVisible();
    expect(
      screen.getByText(/可信匹配任务：旧版未提供，不可推断/),
    ).toBeVisible();
  },
);
it("keeps missing and null execution match counts unknown rather than deriving zero from review counts", async () => {
  const data = page();
  Object.assign(data.coverage, {
    executionObservedQueryCount: null,
    executionUnmatchedQueryHashCount: null,
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(
    await screen.findByText(/执行任务匹配：旧版未提供，不可推断/),
  ).toBeVisible();
  expect(
    screen.getByText(
      /可信匹配任务：旧版未提供，不可推断.*计划任务：旧版未提供，不可推断/,
    ),
  ).toBeVisible();
  expect(
    screen.getByText(/观测查询：未知.*未匹配查询哈希：未知/),
  ).toBeVisible();
  expect(
    screen.getByText(/执行凭证缺口待审方向：旧版未提供，不可推断/),
  ).toBeVisible();
  expect(
    screen.queryByText(
      /可信匹配任务：0|未匹配查询哈希：0|执行凭证缺口待审方向：0/,
    ),
  ).not.toBeInTheDocument();
});
it.each([
  ["RECEIPT_SUMMARIZED_SIZE_LIMIT", "执行凭证因大小限制仅保留摘要"],
  ["RECEIPT_OMITTED_SIZE_LIMIT", "执行凭证因大小限制已省略"],
])(
  "explains bounded receipt transport %s without releasing saved pending directions",
  async (executionReceiptTransportReason, label) => {
    const data = page();
    Object.assign(data.coverage, {
      executionReceiptTransportReason,
      autoPublishAllowed: false,
    });
    vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
    renderWithSession(<ResearchTopicRegistry runId={run} state="FAILED" />);
    expect(await screen.findByText(new RegExp(label))).toBeVisible();
    expect(screen.getByText(/相关观察保留，未凭此放行/)).toBeVisible();
    expect(screen.getByText("自动发布暂缓")).toBeVisible();
    expect(screen.getByText("动态主题0")).toBeVisible();
    expect(screen.queryByText("允许自动发布")).not.toBeInTheDocument();
    expect(api.getResearchTopicRegistry).toHaveBeenCalledOnce();
  },
);
it("shows passed strict calculation separately from withheld auto-publication without granting new actions", async () => {
  const data = page();
  Object.assign(data.coverage, {
    strictRevalidated: true,
    strictDirectionCount: 6,
    autoPublishAllowed: false,
    autoPublishBlockerCount: 2,
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="FAILED" />);
  expect(await screen.findByText("严格计算已通过")).toBeVisible();
  expect(screen.getByText(/严格候选方向：6/)).toBeVisible();
  expect(screen.getByText("自动发布暂缓")).toBeVisible();
  expect(screen.getByText(/发布阻断项：2/)).toBeVisible();
  expect(
    screen.getByText(/下一步：请管理员核对阻断原因，或在后续获授权批次补证/),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /买入|跟踪|执行|重跑/ }),
  ).not.toBeInTheDocument();
});
it("does not infer unavailable layered assurance or publishing outcomes from legacy reviewed counts", async () => {
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  await screen.findByText("动态主题0");
  expect(screen.getByText(/结构齐全范围：旧版未提供，不可推断/)).toBeVisible();
  expect(
    screen.getByText(/执行凭证已核验范围：旧版未提供，不可推断/),
  ).toBeVisible();
  expect(screen.getByText(/严格计算状态：旧版未提供，不可推断/)).toBeVisible();
  expect(screen.getByText(/自动发布状态：旧版未提供，不可推断/)).toBeVisible();
  expect(screen.queryByText(/证据已核验检查：0/)).not.toBeInTheDocument();
});
it("describes an allowed publishing policy without claiming that publication has already occurred", async () => {
  const data = page();
  Object.assign(data.coverage, {
    strictRevalidated: true,
    strictDirectionCount: 1,
    autoPublishAllowed: true,
    autoPublishBlockerCount: 0,
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="FAILED" />);
  expect(await screen.findByText("允许自动发布")).toBeVisible();
  expect(screen.getByText(/实际发布以该研究的正式报告状态为准/)).toBeVisible();
  expect(screen.queryByText("已正式发布")).not.toBeInTheDocument();
});
it("keeps unmatched themes visible with their explanation and without granting investment permission", async () => {
  const data = page();
  data.items = [{ ...row(), match: { ...row().match, status: "UNMATCHED" } }];
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(await screen.findByText(/尚未匹配/)).toBeVisible();
  expect(screen.getByText(/尚未核定/)).toBeVisible();
  expect(screen.getByText(/尚未获正式投资资格/)).toBeVisible();
});
it("shows registered and stored counts and explains an unrecoverable size-only omission", async () => {
  const data = { ...page(), ...serverCounts72 } as api.TopicRegistryPage;
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  const first = renderWithSession(
    <ResearchTopicRegistry runId={run} state="READY" />,
  );
  expect(await screen.findByText(/目录已归档72项/)).toBeVisible();
  expect(screen.getByText(/本批目录登记72项/)).toBeVisible();
  first.unmount();
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue({
    researchId: run,
    registryAvailable: false,
    archiveState: "UNRECOVERABLE",
    items: [],
    actionable: false,
    ...{
      topicArchiveIssue: "REGISTRY_OUTPUT_LIMIT",
      reason: "主题目录超过归档预算，完整冻结材料仍保留。",
    },
  });
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(
    await screen.findByText("主题目录超过归档预算，完整冻结材料仍保留。"),
  ).toBeVisible();
  expect(screen.queryByText(/本批目录登记0/)).not.toBeInTheDocument();
});
it("labels the actual core 72-entry aggregate independently of its 64-discovery budget", async () => {
  // Captured from evaluate_research_batch -> prepare_topic_registry with 64 catalog topics and eight distinct formal keys.
  const data = { ...page(), ...serverCounts72 } as api.TopicRegistryPage;
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(data);
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  expect(
    await screen.findByText("本批目录登记72项（发现与正式方向按key汇总）"),
  ).toBeVisible();
  expect(screen.getByText(/每批64个发现主题/)).toBeVisible();
  expect(screen.getByText(/正式方向每批最多8个/)).toBeVisible();
  expect(screen.queryByText(/本批发现72/)).not.toBeInTheDocument();
});
it("keeps historical 404 quiet without polling and reloads when the same run reaches a new terminal completion", async () => {
  vi.mocked(api.getResearchTopicRegistry)
    .mockRejectedValueOnce(new HttpError("not found", 404))
    .mockResolvedValue(page());
  function Lifecycle() {
    const [done, setDone] = useState(false);
    return (
      <>
        <button onClick={() => setDone(true)}>终态更新</button>
        <ResearchTopicRegistry
          runId={run}
          state={done ? "FAILED" : "READY"}
          completedAt={
            done ? "2026-11-04T09:00:00+08:00" : "2026-11-03T09:00:00+08:00"
          }
        />
      </>
    );
  }
  renderWithSession(<Lifecycle />);
  await waitFor(() =>
    expect(api.getResearchTopicRegistry).toHaveBeenCalledOnce(),
  );
  expect(
    screen.queryByRole("region", { name: /主题目录/ }),
  ).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "终态更新" }));
  expect(await screen.findByText("动态主题0")).toBeVisible();
  expect(api.getResearchTopicRegistry).toHaveBeenCalledTimes(2);
});
it("bounds archive polling at ten checks every three seconds and keeps manual GET recovery", async () => {
  vi.useFakeTimers();
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue({
    researchId: run,
    registryAvailable: false,
    archiveState: "AUDIT_PENDING",
    items: [],
    actionable: false,
  });
  renderWithSession(<ResearchTopicRegistry runId={run} state="FAILED" />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5);
  });
  expect(screen.getByText(/主题目录待归档/)).toBeVisible();
  for (let i = 0; i < 12; i++)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
  const calls = vi.mocked(api.getResearchTopicRegistry).mock.calls.length;
  expect(calls).toBe(11);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(600_000);
  });
  expect(api.getResearchTopicRegistry).toHaveBeenCalledTimes(calls);
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(page());
  await act(async () => {
    screen.getByRole("button", { name: "重新检查主题目录" }).click();
    await vi.advanceTimersByTimeAsync(5);
  });
  expect(screen.getByText("动态主题0")).toBeVisible();
  expect(api.getResearchTopicRegistry).toHaveBeenCalledTimes(calls + 1);
});
it("stops temporary network errors and lets a manual read recover without exposing the exception", async () => {
  vi.useFakeTimers();
  vi.mocked(api.getResearchTopicRegistry).mockRejectedValue(
    new HttpError("PRIVATE_NETWORK", 503),
  );
  renderWithSession(<ResearchTopicRegistry runId={run} state="FAILED" />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4000);
  });
  expect(api.getResearchTopicRegistry).toHaveBeenCalledOnce();
  expect(screen.queryByText("PRIVATE_NETWORK")).not.toBeInTheDocument();
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(page());
  await act(async () => {
    screen.getByRole("button", { name: "重新检查主题目录" }).click();
    await vi.advanceTimersByTimeAsync(5);
  });
  expect(screen.getByText("动态主题0")).toBeVisible();
});
it("never polls a legacy 404 or queries an unfinished uncertain run", async () => {
  vi.useFakeTimers();
  vi.mocked(api.getResearchTopicRegistry).mockRejectedValue(
    new HttpError("missing", 404),
  );
  const first = renderWithSession(
    <ResearchTopicRegistry runId={run} state="READY" />,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3_600_000);
  });
  expect(api.getResearchTopicRegistry).toHaveBeenCalledOnce();
  expect(
    screen.queryByRole("region", { name: /主题目录/ }),
  ).not.toBeInTheDocument();
  first.unmount();
  renderWithSession(<ResearchTopicRegistry runId={run} state="UNCERTAIN" />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_000);
  });
  expect(api.getResearchTopicRegistry).toHaveBeenCalledOnce();
});
it("refuses to combine pages from another snapshot and provides a way back to the original first page", async () => {
  vi.mocked(api.getResearchTopicRegistry).mockImplementation(
    async (_run, options) =>
      options.cursor
        ? {
            ...page(),
            snapshotId: "different-snapshot",
            items: [{ ...row(), title: "不能拼接的页" }],
          }
        : { ...page(), nextCursor: "page-two" },
  );
  renderWithSession(<ResearchTopicRegistry runId={run} state="READY" />);
  await screen.findByText("动态主题0");
  await userEvent.click(screen.getByRole("button", { name: "下一页主题" }));
  expect(
    await screen.findByText("主题目录快照发生变化，请返回第一页核对。"),
  ).toBeVisible();
  expect(screen.queryByText("不能拼接的页")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "返回主题第一页" }));
  expect(await screen.findByText("动态主题0")).toBeVisible();
});
const daily = (): DailyResearch => ({
  methodVersion: "v1",
  state: "FAILED",
  runId: run,
  reportRunId: old,
  reportCompletedAt: "2026-11-03T08:00:00+08:00",
  scheduledFor: null,
  startedAt: null,
  completedAt: "2026-11-04T09:00:00+08:00",
  asOf: null,
  nextRunAt: null,
  report: {
    summary: "旧READY报告保留",
    coverage: [],
    sources: [],
    events: [],
    upcoming: [],
    energy: [],
  },
});
it("retains source-backed current news and dynamic topics when there is no formal report or qualified direction", async () => {
  const registry = page();
  Object.assign(registry.coverage, {
    qualifiedFormalDirectionCount: 0,
    validEventCount: 1,
  });
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue(registry);
  vi.mocked(getResearchAcceptance).mockResolvedValue({
    id: run,
    researchId: run,
    kind: "INITIAL",
    decision: "PARTIAL",
    validatorVersion: "acceptance-v1",
    validatedAt: registry.validatedAt,
    actionable: false,
    formalPublishable: false,
    issueCount: 0,
    issueListComplete: true,
    canInspectArtifacts: false,
    canReplay: false,
    counts: {
      sources: { inputCount: 1, acceptedCount: 1, quarantinedCount: 0 },
      events: { inputCount: 1, acceptedCount: 1, quarantinedCount: 0 },
    },
    accepted: {
      sources: [
        {
          id: "s1",
          title: "本次融资报道原文",
          url: "https://example.test/current-news",
          quote: "融资规模下降。",
          verification: "SOURCE_CHECKED",
        },
      ],
      industryCycleReviews: [],
      valueDirections: [],
      upcoming: [],
      events: [
        {
          id: "news-only",
          title: "零正式方向下的有据新闻",
          summary: "融资变化仍须核查。",
          sourceIds: ["s1"],
        },
      ],
    },
  });
  const data = { ...daily(), reportRunId: null, report: null };
  renderWithSession(<DailyResearchReport data={data} />);
  expect(await screen.findByText("零正式方向下的有据新闻")).toBeVisible();
  expect(await screen.findByText("动态主题0")).toBeVisible();
  expect(screen.getByText(/符合正式资格方向：0/)).toBeVisible();
  expect(
    screen.queryByRole("region", { name: "正式报告主题目录" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /买入|跟踪|推送/ }),
  ).not.toBeInTheDocument();
  expect(data.report).toBeNull();
  expect(api.getResearchTopicRegistry).toHaveBeenCalledOnce();
});
it("keeps the old READY registry separate from the latest failed research observations", async () => {
  vi.mocked(api.getResearchTopicRegistry).mockImplementation(
    async (researchId) => ({
      ...page(researchId),
      items: [
        { ...row(), title: researchId === old ? "旧报告主题" : "本次失败观察" },
      ],
    }),
  );
  renderWithSession(<DailyResearchReport data={daily()} />);
  const formal = await screen.findByRole("region", {
      name: "正式报告主题目录",
    }),
    current = await screen.findByRole("region", { name: "本次研究主题目录" });
  expect(await within(formal).findByText("旧报告主题")).toBeVisible();
  expect(await within(current).findByText("本次失败观察")).toBeVisible();
  expect(within(formal).queryByText("本次失败观察")).not.toBeInTheDocument();
  expect(screen.getByText("旧READY报告保留")).toBeInTheDocument();
});
it("keeps tenant caches separate and hides responses with another research identity", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  const ui = () => (
    <QueryClientProvider client={client}>
      <ResearchTopicRegistry runId={run} state="READY" />
    </QueryClientProvider>
  );
  const first = render(ui());
  await screen.findByText("动态主题0");
  session.tenantId = "tenant-b";
  vi.mocked(api.getResearchTopicRegistry).mockResolvedValue({
    ...page(old),
    items: [{ ...row(), title: "不应出现的其它run内容" }],
  });
  first.rerender(ui());
  await waitFor(() =>
    expect(api.getResearchTopicRegistry).toHaveBeenCalledTimes(2),
  );
  expect(screen.queryByText("不应出现的其它run内容")).not.toBeInTheDocument();
  expect(screen.queryByText("动态主题0")).not.toBeInTheDocument();
  client.clear();
});
it("adds a lightweight actual-report entry without fetching the registry while the global panel is collapsed", async () => {
  renderWithSession(
    <MacroContextStrip
      error={false}
      data={
        {
          state: "READY",
          items: [],
          dailyResearch: daily(),
        } as OpportunityContext
      }
    />,
    "/fund-screener?researchOpen=0",
  );
  expect(
    screen.getByRole("button", { name: "查看展示报告的主题目录" }),
  ).toBeVisible();
  expect(api.getResearchTopicRegistry).not.toHaveBeenCalled();
});

it("opens the displayed report's buy/sell plans independently of the current run", async () => {
  renderWithSession(
    <MacroContextStrip
      error={false}
      data={
        {
          state: "READY",
          items: [],
          dailyResearch: daily(),
        } as OpportunityContext
      }
    />,
    "/fund-screener?researchOpen=0",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "查看展示报告的买卖计划" }),
  );
  const plans = await screen.findByRole("group", {
    name: "展示报告的买卖计划",
  });
  await waitFor(() => expect(plans.closest("details")).toHaveAttribute("open"));
  expect(plans).toHaveAttribute("data-report-run-id", daily().reportRunId);
  expect(plans).toBeVisible();
  expect(
    document.getElementById(`topic-catalog-${daily().reportRunId}`),
  ).not.toHaveAttribute("open");
});

import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithSession } from "../../test/render-with-session";
import * as api from "../../api/history-repairs";
import { getResearchAcceptance } from "../../api/research-acceptance";
import ResearchHistoryRepairs, {
  PublishedResearchRepairs,
} from "./components/ResearchHistoryRepairs";
import DailyResearchReport from "../fund-screener/components/DailyResearchReport";

const session = vi.hoisted(() => ({
  tenantId: "tenant-a",
  userId: "reviewer-a",
  role: "OWNER",
}));
vi.mock("../../app/auth-session", async (original) => ({
  ...(await original<typeof import("../../app/auth-session")>()),
  useAuthSession: () => session,
}));
vi.mock("../../api/history-repairs", () => ({
  previewHistoricalRepair: vi.fn(),
  listHistoricalRepairs: vi.fn(),
  getHistoricalRepair: vi.fn(),
  generateHistoricalRepair: vi.fn(),
  reviewHistoricalRepair: vi.fn(),
  publishHistoricalRepair: vi.fn(),
  getHistoricalRepairDetails: vi.fn(),
}));
vi.mock("../../api/research-acceptance", () => ({
  getResearchAcceptance: vi.fn(),
}));
const source = "ecdf9dce-54e4-4b26-98ab-e1af6f4e1ffb";
const id = "abbc3057-104b-4546-a6b2-ed560f6049f7";
const fixture = (
  state: api.HistoricalRepair["state"] = "DRAFT",
): api.HistoricalRepair => ({
  id,
  sourceRunId: source,
  kind: "HISTORICAL_REPAIR",
  state,
  publicationScope: "HISTORICAL_READ_ONLY",
  actionable: false,
  originalStatus: "FAILED",
  currentFreshness: "EXPIRED",
  validatedAt: "2026-10-10T08:00:00+08:00",
  decision: "REPAIRED",
  strictPass: true,
  autoPublishAllowed: false,
  historicalPublishAllowed: true,
  safetyBlocked: false,
  repairVersion: "repair-v1",
  canGenerate: true,
  canInspectRepairDetails: true,
  canReview: state === "DRAFT",
  canReject: state === "DRAFT",
  canPublish: state === "APPROVED",
  creatorId: "creator-b",
  reviewerId: state === "APPROVED" ? "reviewer-a" : undefined,
  accepted: {
    macroOverview: {
      summary: "历史信用观察摘要",
      verification: "MODEL_UNVERIFIED",
      sourceIds: ["s1"],
      coverage: [],
    },
    sources: [],
    industryCycleReviews: [],
    valueDirections: [],
    events: [],
    upcoming: [],
  },
  quarantined: [
    {
      itemType: "EVENT",
      itemIndex: 1,
      path: "PRIVATE-PATH",
      businessKey: "PRIVATE-KEY",
      ruleId: "INVALID_EVENT",
      reason: "该事件引用不足，已隔离。",
      title: "被隔离的历史事件",
      summary: "保留历史报道线索，不作事实确认。",
      importance: "HIGH",
      sourceIds: ["s1"],
      sourceEvidence: [
        {
          id: "s1",
          title: "冻结新闻依据",
          url: "https://example.test/frozen",
          quote: "冻结的原始引文",
          verification: "UNVERIFIED",
        },
      ],
    },
  ],
  retained: { counts: { events: 1 }, directionKeys: ["retained-direction"] },
  sourceDigests: { raw: "a".repeat(64) },
  originalOutcome: {
    researchId: source,
    decision: "PARTIAL",
    issueCount: 1,
    issueListComplete: true,
    issues: [
      {
        issueId: "issue-1",
        researchId: source,
        itemType: "EVENT",
        path: "events[1]",
        message: "固定原验收问题",
        disposition: "QUARANTINED",
      },
    ],
  },
});
beforeEach(() => {
  vi.resetAllMocks();
  session.role = "OWNER";
  sessionStorage.clear();
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [],
    canGenerate: true,
  });
  vi.mocked(api.previewHistoricalRepair).mockResolvedValue({
    ...fixture("PREVIEW"),
    id: undefined,
  });
  vi.mocked(api.generateHistoricalRepair).mockResolvedValue({
    ...fixture(),
    creatorId: session.userId,
    canReview: false,
  });
  vi.mocked(api.getHistoricalRepair).mockResolvedValue(fixture());
});

it("opens the original opportunity simulation directly in a new tab without creating a repair", async () => {
  renderWithSession(<ResearchHistoryRepairs sourceRunId={source} />);
  const link = await screen.findByRole("link", { name: "预览投资机会页面" });
  expect(link).toHaveAttribute("href", `/operations/research-preview/${source}`);
  expect(link).toHaveAttribute("target", "_blank");
  expect(link.getAttribute("rel")).toContain("noopener");
  expect(api.previewHistoricalRepair).not.toHaveBeenCalled();
  expect(api.generateHistoricalRepair).not.toHaveBeenCalled();
  expect(api.reviewHistoricalRepair).not.toHaveBeenCalled();
  expect(api.publishHistoricalRepair).not.toHaveBeenCalled();
});

it("keeps direct simulation disabled until a valid original research identity is entered", async () => {
  renderWithSession(<ResearchHistoryRepairs />);
  expect(await screen.findByRole("button", { name: "预览投资机会页面" })).toBeDisabled();
  await userEvent.type(screen.getByRole("textbox", { name: "原始研究 ID" }), source);
  expect(screen.getByRole("link", { name: "预览投资机会页面" })).toHaveAttribute("href", `/operations/research-preview/${source}`);
});

it("shows only published historical observations and public quarantine evidence without trading or admin details", async () => {
  session.role = "MEMBER";
  const published = fixture("PUBLISHED");
  Object.assign(published.accepted, {
    plans: [{ entryZoneMin: "PRIVATE_PRICE" }],
  });
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [fixture(), published],
    canGenerate: false,
  });
  renderWithSession(<PublishedResearchRepairs />);
  expect(await screen.findByText("历史信用观察摘要")).toBeVisible();
  expect(screen.getByText("历史只读研究 · 禁止用于实时买入")).toBeVisible();
  expect(screen.getByText("被隔离的历史事件")).toBeVisible();
  expect(screen.getByText("冻结的原始引文")).toBeVisible();
  expect(screen.getByRole("link", { name: "冻结新闻依据" })).toHaveAttribute(
    "href",
    "https://example.test/frozen",
  );
  expect(
    screen.queryByText(/PRIVATE-PATH|PRIVATE-KEY|PRIVATE_PRICE|固定原验收问题/),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /生成|审核|发布|跟踪|买入|推送/ }),
  ).not.toBeInTheDocument();
  expect(api.previewHistoricalRepair).not.toHaveBeenCalled();
  expect(api.generateHistoricalRepair).not.toHaveBeenCalled();
  expect(api.getHistoricalRepairDetails).not.toHaveBeenCalled();
});

it("makes published history readable from the ordinary daily-report surface without the operations route", async () => {
  session.role = "MEMBER";
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [fixture("PUBLISHED")],
    canGenerate: false,
  });
  renderWithSession(<DailyResearchReport />);
  expect(await screen.findByText("历史信用观察摘要")).toBeVisible();
  expect(screen.getByText("暂无有效日报")).toBeVisible();
  expect(
    screen.queryByRole("link", { name: "管理历史研究修复" }),
  ).not.toBeInTheDocument();
});

it("previews with a GET and generates only on an explicit click without allowing the creator to review", async () => {
  renderWithSession(<ResearchHistoryRepairs sourceRunId={source} />);
  await screen.findByRole("button", { name: "检查修复条件（只读）" });
  expect(api.previewHistoricalRepair).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "检查修复条件（只读）" }),
  );
  expect(await screen.findByText("历史信用观察摘要")).toBeVisible();
  expect(api.previewHistoricalRepair).toHaveBeenCalledWith(
    source,
    expect.any(AbortSignal),
  );
  expect(api.generateHistoricalRepair).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "生成修复草稿" }));
  expect(await screen.findByText("须由其他管理员独立审核。")).toBeVisible();
  expect(api.generateHistoricalRepair).toHaveBeenCalledWith(
    source,
    expect.stringMatching(/^[a-f0-9-]{36}$/i),
  );
  expect(api.reviewHistoricalRepair).not.toHaveBeenCalled();
  expect(api.publishHistoricalRepair).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "批准修复版本" }),
  ).not.toBeInTheDocument();
});

it("requires a separate reviewer click and an explicit historical publish without changing the original FAILED identity", async () => {
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [fixture()],
    canGenerate: true,
  });
  vi.mocked(api.reviewHistoricalRepair).mockResolvedValue(fixture("APPROVED"));
  vi.mocked(api.publishHistoricalRepair).mockResolvedValue(
    fixture("PUBLISHED"),
  );
  renderWithSession(<ResearchHistoryRepairs sourceRunId={source} />);
  await userEvent.click(
    await screen.findByRole("button", { name: /查看修复版本/ }),
  );
  expect(await screen.findByText("固定原验收问题")).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "批准修复版本" }));
  const publish = await screen.findByRole("button", {
    name: "发布历史只读摘要",
  });
  expect(api.publishHistoricalRepair).not.toHaveBeenCalled();
  await userEvent.click(publish);
  expect(await screen.findByText("状态：已发布")).toBeVisible();
  expect(
    screen.getByText(`原始研究：${source} · 原始状态：FAILED`),
  ).toBeVisible();
  expect(api.reviewHistoricalRepair).toHaveBeenCalledWith(
    id,
    expect.any(String),
    "APPROVE",
  );
  expect(api.publishHistoricalRepair).toHaveBeenCalledWith(
    id,
    expect.any(String),
  );
  expect(screen.queryByText("研究成功 · 报告已生成")).not.toBeInTheDocument();
});

it("never provides a manual publish override for a hard-blocked historical candidate", async () => {
  const blocked = {
    ...fixture(),
    strictPass: false,
    safetyBlocked: true,
    canReview: false,
    canPublish: false,
    hardBlockers: [{ code: "UNSAFE_SOURCE", reason: "来源不符合安全边界。" }],
  };
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [blocked],
    canGenerate: true,
  });
  vi.mocked(api.getHistoricalRepair).mockResolvedValue(blocked);
  renderWithSession(<ResearchHistoryRepairs sourceRunId={source} />);
  await userEvent.click(
    await screen.findByRole("button", { name: /查看修复版本/ }),
  );
  expect(await screen.findByText("来源不符合安全边界。")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /批准修复版本|发布历史只读摘要/ }),
  ).not.toBeInTheDocument();
});

it("allows a different authorized administrator to reject a hard-blocked draft while keeping approval and publish blocked", async () => {
  const blocked: api.HistoricalRepair = {
    ...fixture(),
    strictPass: false,
    safetyBlocked: true,
    historicalPublishAllowed: false,
    canReview: false,
    canPublish: false,
    canReject: true,
    hardBlockers: [{ code: "UNSAFE_SOURCE", reason: "来源不符合安全边界。" }],
  };
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [blocked],
    canGenerate: true,
  });
  vi.mocked(api.getHistoricalRepair).mockResolvedValue(blocked);
  vi.mocked(api.reviewHistoricalRepair).mockResolvedValue({
    ...blocked,
    state: "REJECTED",
    canReject: false,
  });
  renderWithSession(<ResearchHistoryRepairs sourceRunId={source} />);
  await userEvent.click(
    await screen.findByRole("button", { name: /查看修复版本/ }),
  );
  const reject = await screen.findByRole("button", { name: "拒绝修复版本" });
  expect(
    screen.queryByRole("button", { name: /批准修复版本|发布历史只读摘要/ }),
  ).not.toBeInTheDocument();
  expect(api.reviewHistoricalRepair).not.toHaveBeenCalled();
  await userEvent.click(reject);
  expect(await screen.findByText("状态：已拒绝")).toBeVisible();
  expect(api.reviewHistoricalRepair).toHaveBeenCalledWith(
    id,
    expect.any(String),
    "REJECT",
  );
  expect(api.publishHistoricalRepair).not.toHaveBeenCalled();
});

it("reuses the generation command key after a timeout and remount instead of automatically retrying", async () => {
  vi.mocked(api.generateHistoricalRepair).mockRejectedValueOnce(
    new Error("PRIVATE_TIMEOUT"),
  );
  const first = renderWithSession(
    <ResearchHistoryRepairs sourceRunId={source} />,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "检查修复条件（只读）" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "生成修复草稿" }),
  );
  await screen.findByText("操作结果尚未确认，请重试同一操作。");
  expect(api.generateHistoricalRepair).toHaveBeenCalledOnce();
  const key = vi.mocked(api.generateHistoricalRepair).mock.calls[0][1];
  first.unmount();
  renderWithSession(<ResearchHistoryRepairs sourceRunId={source} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "检查修复条件（只读）" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "生成修复草稿" }),
  );
  await screen.findByText("须由其他管理员独立审核。");
  expect(vi.mocked(api.generateHistoricalRepair).mock.calls[1][1]).toBe(key);
  expect(screen.queryByText("PRIVATE_TIMEOUT")).not.toBeInTheDocument();
});

it("keeps the source editor usable while a new identity is incomplete and previews only the explicit target", async () => {
  renderWithSession(<ResearchHistoryRepairs sourceRunId={source} />);
  const input = await screen.findByRole("textbox", { name: "原始研究 ID" });
  await userEvent.clear(input);
  await userEvent.type(
    screen.getByRole("textbox", { name: "原始研究 ID" }),
    source,
  );
  expect(api.previewHistoricalRepair).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "检查修复条件（只读）" }),
  );
  expect(await screen.findByText("历史信用观察摘要")).toBeVisible();
  expect(api.previewHistoricalRepair).toHaveBeenCalledOnce();
  expect(api.previewHistoricalRepair).toHaveBeenCalledWith(
    source,
    expect.any(AbortSignal),
  );
});

it("does not offer self-review even when a stale response advertises review permission", async () => {
  const own = { ...fixture(), creatorId: session.userId, canReview: true };
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [own],
    canGenerate: true,
  });
  vi.mocked(api.getHistoricalRepair).mockResolvedValue(own);
  renderWithSession(<ResearchHistoryRepairs sourceRunId={source} />);
  await userEvent.click(
    await screen.findByRole("button", { name: /查看修复版本/ }),
  );
  expect(await screen.findByText("须由其他管理员独立审核。")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /批准修复版本|拒绝修复版本/ }),
  ).not.toBeInTheDocument();
});

it("does not attach a late generation acknowledgement to a different source selection", async () => {
  let resolve!: (value: api.HistoricalRepair) => void;
  vi.mocked(api.generateHistoricalRepair).mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  renderWithSession(<ResearchHistoryRepairs sourceRunId={source} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "检查修复条件（只读）" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "生成修复草稿" }),
  );
  await userEvent.clear(screen.getByRole("textbox", { name: "原始研究 ID" }));
  resolve(fixture());
  await screen.findByRole("button", { name: "检查修复条件（只读）" });
  expect(screen.queryByText("历史信用观察摘要")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "批准修复版本" }),
  ).not.toBeInTheDocument();
});

it("keeps quarantined original evidence as escaped text and rejects unsafe links", async () => {
  session.role = "MEMBER";
  const row = fixture("PUBLISHED");
  row.quarantined![0].sourceEvidence![0] = {
    id: "unsafe",
    title: "不可打开的依据",
    url: "javascript:alert(1)",
    quote: "<script>not executable</script>",
  };
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [row],
    canGenerate: false,
  });
  const view = renderWithSession(<PublishedResearchRepairs />);
  expect(
    await screen.findByText("<script>not executable</script>"),
  ).toBeVisible();
  expect(
    screen.queryByRole("link", { name: "不可打开的依据" }),
  ).not.toBeInTheDocument();
  expect(view.container.querySelector("script")).toBeNull();
});

it("labels an excerpted historical summary instead of presenting it as complete original content", async () => {
  session.role = "MEMBER";
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [{ ...fixture("PUBLISHED"), acceptedContentComplete: false }],
    canGenerate: false,
  });
  renderWithSession(<PublishedResearchRepairs />);
  expect(
    await screen.findByText("验收正文为节选，完整内容请管理员查看冻结材料"),
  ).toBeVisible();
});

it("loads original INITIAL admin issue details only on demand and keeps them bound to the source run", async () => {
  const row = fixture();
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [row],
    canGenerate: true,
  });
  vi.mocked(getResearchAcceptance).mockResolvedValue({
    id: "initial",
    researchId: source,
    kind: "INITIAL",
    decision: "PARTIAL",
    validatorVersion: "original-v1",
    validatedAt: row.validatedAt,
    formalPublishable: false,
    actionable: false,
    canInspectArtifacts: true,
    canReplay: false,
    counts: {},
    accepted: row.accepted,
    issueCount: 1,
    issueListComplete: true,
    issues: [
      {
        issueId: "original-issue",
        researchId: source,
        itemType: "FUND_PLAN",
        disposition: "BLOCKED",
        message: "原始计划未完成行业核查。",
        businessKey: "fund:PRIVATE-ADMIN-DETAIL",
        missingIndustryCodes: ["NONFERROUS"],
      },
    ],
  });
  renderWithSession(<ResearchHistoryRepairs sourceRunId={source} />);
  await userEvent.click(
    await screen.findByRole("button", { name: /查看修复版本/ }),
  );
  expect(await screen.findByText("固定原验收问题")).toBeVisible();
  expect(getResearchAcceptance).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "读取原始验收完整问题" }),
  );
  expect(await screen.findByText("fund:PRIVATE-ADMIN-DETAIL")).toBeVisible();
  expect(screen.getByText("原始计划未完成行业核查。")).toBeVisible();
  expect(getResearchAcceptance).toHaveBeenCalledWith(
    source,
    expect.any(AbortSignal),
  );
});

it("keeps high-importance quarantined risk and scenario evidence visibly readable in the public annex", async () => {
  session.role = "MEMBER";
  const row = fixture("PUBLISHED");
  Object.assign(row.quarantined![0], {
    importance: "HIGH",
    risks: [{ summary: "原始盈利假设仍有证据缺口。", sourceIds: ["s1"] }],
    watchFor: ["观察原定订单实际回款"],
    unknowns: ["量产时间尚未核实"],
    channels: ["融资变化向订单传导"],
    scenarios: [
      {
        condition: "若融资持续下降",
        impact: "原交付假设可能推迟",
        counter: "需核查真实客户回款",
      },
    ],
  });
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [row],
    canGenerate: false,
  });
  renderWithSession(<PublishedResearchRepairs />);
  expect(
    await screen.findByText("高重要性 · 隔离材料须完整核对"),
  ).toBeVisible();
  for (const text of [
    "原始盈利假设仍有证据缺口。",
    "观察原定订单实际回款",
    "量产时间尚未核实",
    "融资变化向订单传导",
    "若融资持续下降",
    "原交付假设可能推迟",
    "需核查真实客户回款",
  ])
    expect(screen.getByText(text, { exact: false })).toBeVisible();
  expect(
    screen.queryByRole("button", { name: /跟踪|买入|推送/ }),
  ).not.toBeInTheDocument();
});

it("keeps quarantined catalysts and unverified industry-cycle evidence gaps expanded and observable", async () => {
  session.role = "MEMBER";
  const row = fixture("PUBLISHED");
  Object.assign(row.quarantined![0], {
    mandatory: true,
    catalysts: [{ summary: "原先政策催化仍须等待传导。", sourceIds: ["s1"] }],
    industryCycle: {
      stage: "UNKNOWN",
      riskLevel: "SUSPECTED",
      suspectedRiskFlags: ["FINANCING_COOLING"],
      evidenceGaps: ["INDUSTRY_SCOPE_UNVERIFIED:FINANCING_COOLING"],
      evidenceSummary: "原始融资线索的行业范围仍待核实。",
    },
  });
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [row],
    canGenerate: false,
  });
  const view = renderWithSession(<PublishedResearchRepairs />);
  expect(await screen.findByText("原先政策催化仍须等待传导。")).toBeVisible();
  expect(
    screen.getByText("原始融资线索的行业范围仍待核实。", { exact: false }),
  ).toBeVisible();
  expect(screen.getByText("行业风险：待核实")).toBeVisible();
  expect(
    screen.getByText("融资降温证据的行业归属待核实", { exact: false }),
  ).toBeVisible();
  expect(view.container.querySelector("details:not([open])")).toBeNull();
  expect(api.getHistoricalRepairDetails).not.toHaveBeenCalled();
});

it("loads full frozen scoring materials only for an explicit administrator detail GET and renders raw text escaped", async () => {
  vi.mocked(api.listHistoricalRepairs).mockResolvedValue({
    items: [fixture()],
    canGenerate: true,
  });
  vi.mocked(api.getHistoricalRepairDetails).mockResolvedValue({
    id,
    sourceRunId: source,
    state: "DRAFT",
    validatedAt: fixture().validatedAt,
    createdAt: fixture().validatedAt,
    publicationScope: "HISTORICAL_READ_ONLY",
    actionable: false,
    candidateDigest: "c".repeat(64),
    sourceDigests: {},
    provenance: {},
    privateResult: {
      formalReport: {
        scoring: { stopLossPrice: "ADMIN_SCORE_PRICE" },
        summary: "<script>raw frozen text</script>",
      },
    },
  });
  const view = renderWithSession(
    <ResearchHistoryRepairs sourceRunId={source} />,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: /查看修复版本/ }),
  );
  await screen.findByText("固定原验收问题");
  expect(api.getHistoricalRepairDetails).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "查看管理员完整修复材料" }),
  );
  const raw = await screen.findByLabelText("管理员完整修复材料");
  expect(raw).toHaveTextContent("ADMIN_SCORE_PRICE");
  expect(raw).toHaveTextContent("<script>raw frozen text</script>");
  expect(view.container.querySelector("script")).toBeNull();
  expect(api.getHistoricalRepairDetails).toHaveBeenCalledWith(
    id,
    expect.any(AbortSignal),
  );
  expect(api.reviewHistoricalRepair).not.toHaveBeenCalled();
  expect(api.publishHistoricalRepair).not.toHaveBeenCalled();
});

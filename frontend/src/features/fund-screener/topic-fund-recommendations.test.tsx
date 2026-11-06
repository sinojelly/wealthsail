import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { DailyResearch, OpportunityContext } from "../../api/investment";
import { HttpError } from "../../api/client";
import { renderWithSession } from "../../test/render-with-session";
import MacroContextStrip from "./components/MacroContextStrip";

vi.mock("../../app/auth-session", async (original) => ({
  ...(await original<typeof import("../../app/auth-session")>()),
  useAuthSession: () => ({
    tenantId: "reader-tenant",
    userId: "reader",
    role: "MEMBER",
  }),
}));
vi.mock("../../api/research-topic-registry", () => ({
  getResearchTopicRegistry: vi
    .fn()
    .mockRejectedValue(new HttpError("missing", 404)),
}));
vi.mock("../../api/research-acceptance", () => ({
  getResearchAcceptance: vi
    .fn()
    .mockRejectedValue(new HttpError("missing", 404)),
}));
vi.mock("../../api/history-repairs", () => ({
  listHistoricalRepairs: vi.fn().mockResolvedValue({ items: [] }),
}));

const run = "ecdf9dce-54e4-4b26-98ab-e1af6f4e1ffb";
const at = "2026-11-06T08:00:00+08:00";
const response = () => ({
  researchId: run,
  validatedAt: at,
  evaluatedAt: at,
  actionable: false,
  coverage: { topicCount: 3, matchedTopicCount: 3, recommendedFundCount: 3 },
  items: [
    {
      topicId: "robot",
      key: "robot",
      title: "机器人订单机会",
      thesis: "订单增长可能传导至产业链盈利。",
      sourceIds: ["s1"],
      riskStatus: "UNVERIFIED_RISK",
      riskWarnings: ["搜索执行凭证尚未核验"],
      recommendationStatus: "MATCHED",
      missingEvidence: ["RISK_CHECKS_PENDING"],
      actionable: false,
      recommendedFunds: [
        {
          fundCode: "000001",
          name: "机器人主题基金",
          matchLevel: "DYNAMIC_MATCH",
          matchReason: "跟踪指数覆盖机器人产业链",
          subscription: { status: "UNKNOWN", restricted: null },
          quantitativeScore: null,
          missingEvidence: ["NAV_HISTORY_MISSING"],
        },
      ],
    },
    {
      topicId: "chip",
      key: "chip",
      title: "芯片产业机会",
      thesis: "资本开支改善。",
      sourceIds: ["s2"],
      riskStatus: "RISK_ALERT",
      riskWarnings: ["短期产能过剩与价格竞争"],
      recommendationStatus: "MATCHED",
      missingEvidence: [],
      actionable: false,
      recommendedFunds: [
        {
          fundCode: "000002",
          name: "芯片精选基金",
          matchLevel: "CORE_MATCH",
          matchReason: "核心观察池同行业匹配",
          subscription: { status: "OPEN", restricted: false },
          quantitativeScore: 75,
          missingEvidence: [],
        },
      ],
    },
    {
      topicId: "macro",
      key: "macro",
      title: "宏观需求改善",
      thesis: "需求回暖利于大盘。",
      sourceIds: ["s3"],
      riskStatus: "VERIFIED_SAFE",
      riskWarnings: [],
      recommendationStatus: "MATCHED",
      missingEvidence: [],
      actionable: false,
      recommendedFunds: [
        {
          fundCode: "000003",
          name: "宽基指数基金",
          matchLevel: "BROAD_FALLBACK",
          matchReason: "通过宽基承接宏观改善逻辑",
          subscription: { status: "OPEN", restricted: false },
          quantitativeScore: 60,
          missingEvidence: [],
        },
      ],
    },
  ],
});
const daily = (): DailyResearch =>
  ({
    state: "FAILED",
    runId: run,
    completedAt: at,
    report: null,
  }) as DailyResearch;
function ui(data = daily()) {
  return (
    <MacroContextStrip
      error={false}
      data={
        {
          state: "READY",
          items: [],
          dailyResearch: data,
        } as unknown as OpportunityContext
      }
    />
  );
}
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify({ data: response(), meta: {} }), {
          status: 200,
        }),
    ),
  );
});
afterEach(() => vi.unstubAllGlobals());

it("connects news opportunities to funds despite no formal report or incomplete risk checks", async () => {
  renderWithSession(ui());
  const region = await screen.findByRole("region", {
    name: "新闻机会与匹配基金",
  });
  expect(await within(region).findByText("机器人订单机会")).toBeVisible();
  expect(within(region).getByText(/000001.*机器人主题基金/)).toBeVisible();
  expect(within(region).getByText("负面风险待核验")).toBeVisible();
  expect(within(region).getByText("存在负面分歧")).toBeVisible();
  expect(within(region).getByText("风险核查已完成")).toBeVisible();
  expect(within(region).getByText("精选池匹配")).toBeVisible();
  expect(within(region).getByText("全量库动态匹配")).toBeVisible();
  expect(within(region).getByText("宽基兜底匹配")).toBeVisible();
  expect(within(region).getByText("短期产能过剩与价格竞争")).toBeVisible();
  expect(within(region).getByText(/申购状态待核验/)).toBeVisible();
  expect(
    within(region).queryByRole("button", { name: /买入|跟踪|执行/ }),
  ).not.toBeInTheDocument();
  expect(
    within(region).queryByText(/立即买入|已排除所有风险/),
  ).not.toBeInTheDocument();
});

it("does not request recommendations until the investment panel opens", async () => {
  renderWithSession(ui(), "/fund-screener?researchOpen=0");
  expect(fetch).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: /今日投资观察/ }));
  expect(
    await screen.findByRole("region", { name: "新闻机会与匹配基金" }),
  ).toBeVisible();
});

it("keeps subscription restrictions visible and does not present incomplete matching as zero opportunities", async () => {
  const data = response();
  data.items[0].recommendedFunds[0].subscription = {
    status: "LIMITED",
    restricted: true,
  };
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(JSON.stringify({ data, meta: {} })),
  );
  renderWithSession(ui());
  expect(await screen.findByText(/申购限额，需核对销售渠道/)).toBeVisible();
});

it("explains unavailable archived matching without inferring that the research has no funds", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(
      JSON.stringify({
        data: {
          ...response(),
          status: "UNAVAILABLE",
          archiveState: "PENDING_ARCHIVE",
          items: [],
          missingEvidence: ["TOPIC_REGISTRY_UNAVAILABLE"],
          coverage: {
            topicCount: 0,
            matchedTopicCount: 0,
            recommendedFundCount: 0,
          },
        },
        meta: {},
      }),
    ),
  );
  renderWithSession(ui());
  expect(await screen.findByText(/不能据此判断没有机会或基金/)).toBeVisible();
  expect(
    screen.queryByText("本次尚无可展示的机会主题，研究主题目录仍可阅读。"),
  ).not.toBeInTheDocument();
});

it("can reread a pending archive after it completes without reopening the panel", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(
      JSON.stringify({
        data: {
          ...response(),
          status: "UNAVAILABLE",
          archiveState: "AUDIT_PENDING",
          items: [],
          missingEvidence: ["TOPIC_ARCHIVE_PENDING"],
          coverage: {
            topicCount: 0,
            matchedTopicCount: 0,
            recommendedFundCount: 0,
          },
        },
        meta: {},
      }),
    ),
  );
  renderWithSession(ui());
  await screen.findByText(/不能据此判断没有机会或基金/);
  await userEvent.click(
    screen.getByRole("button", { name: "重新检查机会基金匹配" }),
  );
  expect(await screen.findByText("机器人订单机会")).toBeVisible();
  expect(screen.getByText(/000001.*机器人主题基金/)).toBeVisible();
  expect(
    screen.queryByText(/不能据此判断没有机会或基金/),
  ).not.toBeInTheDocument();
});

it("retains matched funds while identifying missing positive source verification", async () => {
  const data = response();
  data.items[0].recommendationStatus = "SOURCE_PENDING";
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(JSON.stringify({ data, meta: {} })),
  );
  renderWithSession(ui());
  expect(
    await screen.findByText("正面来源待核验，以下基金仅为主题匹配候选。"),
  ).toBeVisible();
  expect(screen.getByText(/000001.*机器人主题基金/)).toBeVisible();
});

it("keeps unfinished research quiet and refuses a different research response", async () => {
  const unfinished = renderWithSession(ui({ ...daily(), state: "RUNNING" }));
  expect(fetch).not.toHaveBeenCalled();
  unfinished.unmount();
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(
      JSON.stringify({
        data: {
          ...response(),
          researchId: "abbc3057-104b-4546-a6b2-ed560f6049f7",
        },
        meta: {},
      }),
    ),
  );
  renderWithSession(ui());
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "身份或结构不完整",
  );
  expect(screen.queryByText("机器人订单机会")).not.toBeInTheDocument();
});

it("recovers from a failed read through the explicit retry control", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(
      JSON.stringify({
        error: { code: "TEMPORARY", message: "private connection failure" },
      }),
      { status: 503 },
    ),
  );
  renderWithSession(ui());
  await userEvent.click(
    await screen.findByRole("button", { name: "重新检查机会基金匹配" }),
  );
  expect(await screen.findByText("机器人订单机会")).toBeVisible();
  expect(
    screen.queryByText("private connection failure"),
  ).not.toBeInTheDocument();
});

it("shows an unknown subscription as unverified even when purchase protection is restricted", async () => {
  const data = response();
  data.items[0].recommendedFunds[0].subscription = {
    status: "UNKNOWN",
    restricted: true,
  };
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(JSON.stringify({ data, meta: {} })),
  );
  renderWithSession(ui());
  expect(await screen.findByText(/申购状态待核验/)).toBeVisible();
  expect(
    screen.queryByText(/申购受限，需核对销售渠道/),
  ).not.toBeInTheDocument();
});

it("shows the adverse risk deduction alongside the news thesis and matched fund", async () => {
  const data = response();
  Object.assign(data.items[1], { riskScorePenalty: 20 });
  Object.assign(data.items[1].recommendedFunds[0], {
    unadjustedQuantitativeScore: 95,
  });
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(JSON.stringify({ data, meta: {} })),
  );
  renderWithSession(ui());
  expect(await screen.findByText("负面风险扣分：-20")).toBeVisible();
  expect(screen.getByText(/量化得分：75（扣分前95分）/)).toBeVisible();
  expect(screen.getByText(/000002.*芯片精选基金/)).toBeVisible();
});

it("shows the concrete matching reason when no explainable asset mapping exists", async () => {
  const data = response();
  data.items = [data.items[0]];
  data.items[0].recommendedFunds = [];
  data.items[0].recommendationStatus = "NO_MATCH";
  Object.assign(data.items[0], {
    matchMessage: "当前缺少可说明的地域或资产关联",
  });
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(JSON.stringify({ data, meta: {} })),
  );
  renderWithSession(ui());
  expect(
    await screen.findByText(/当前缺少可说明的地域或资产关联/),
  ).toBeVisible();
});

import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { DailyResearch, OpportunityContext } from "../../api/investment";
import { HttpError } from "../../api/client";
import { renderWithSession } from "../../test/render-with-session";
import MacroContextStrip from "./components/MacroContextStrip";
import TopicFundRecommendations from "./components/TopicFundRecommendations";
import { getResearchTopicRegistry } from "../../api/research-topic-registry";

const session = vi.hoisted(() => ({
  tenantId: "reader-tenant",
  userId: "reader",
  role: "MEMBER",
  authenticated: true,
}));

vi.mock("./components/FundNavTrendChart", () => ({
  default: ({ fundCode, track }: { fundCode: string; track?: unknown }) => (
    <div
      role="img"
      aria-label={`${fundCode}单位净值走势`}
      data-has-price-plan={track !== undefined}
    />
  ),
}));

vi.mock("../../app/auth-session", async (original) => ({
  ...(await original<typeof import("../../app/auth-session")>()),
  useAuthSession: () => (session.authenticated ? session : null),
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
  session.tenantId = "reader-tenant";
  session.authenticated = true;
  vi.mocked(getResearchTopicRegistry).mockClear();
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

it("keeps a missing matching archive visible without calling it zero opportunities", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(
      JSON.stringify({ error: { code: "NOT_FOUND", message: "missing" } }),
      { status: 404 },
    ),
  );
  renderWithSession(
    <TopicFundRecommendations runId={run} state="READY" historical />,
  );
  const region = screen.getByRole("region", { name: "高价值机会" });
  expect(
    await within(region).findByText(/未找到该研究的基金匹配归档记录/),
  ).toBeVisible();
  expect(within(region).getByText(/缺少记录不代表没有基金机会/)).toBeVisible();
  expect(within(region).getByText("最近有效报告 · 历史 · 只读")).toBeVisible();
  expect(
    within(region).getByRole("button", { name: "重新检查机会基金匹配" }),
  ).toBeEnabled();
});

it("shows the current research source while waiting without reading unfinished matching", async () => {
  renderWithSession(
    <TopicFundRecommendations
      runId={run}
      state="RUNNING"
      source="CURRENT"
      completedAt={at}
    />,
  );
  const region = screen.getByRole("region", { name: "本次研究基金观察" });
  expect(within(region).getByText("本次研究 · 只读观察线索")).toBeVisible();
  expect(
    within(region).getByText(/研究尚未完成，基金匹配等待研究结果归档/),
  ).toBeVisible();
  expect(within(region).getByText(new RegExp(run))).not.toBeVisible();
  await userEvent.click(within(region).getByText("报告来源与匹配说明"));
  expect(within(region).getByText(new RegExp(run))).toBeVisible();
  expect(within(region).getByText(/来源时间/)).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
});

it("reads the last completed current research archive when today's state is stale", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(
      JSON.stringify({ error: { code: "NOT_FOUND", message: "missing" } }),
      { status: 404 },
    ),
  );
  renderWithSession(
    <TopicFundRecommendations
      runId={run}
      state="STALE"
      source="CURRENT"
      completedAt={at}
    />,
  );
  const region = screen.getByRole("region", { name: "本次研究基金观察" });
  expect(
    await within(region).findByText(/未找到该研究的基金匹配归档记录/),
  ).toBeVisible();
  expect(
    within(region).getByText(/最近一次研究已结束；今日尚未更新/),
  ).toBeVisible();
  expect(within(region).queryByText(/研究尚未完成/)).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledOnce();
  expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain(
    `/topic-fund-recommendations/${run}`,
  );
});

it.each([null, "invalid-date"])(
  "waits for proof of completion before reading stale current research (%s)",
  (completedAt) => {
    renderWithSession(
      <TopicFundRecommendations
        runId={run}
        state="STALE"
        source="CURRENT"
        completedAt={completedAt}
      />,
    );
    expect(
      screen.getByText(/研究尚未完成，基金匹配等待研究结果归档/),
    ).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  },
);

it("does not treat stale report metadata as a completed report", () => {
  renderWithSession(
    <TopicFundRecommendations
      runId={run}
      state="STALE"
      source="REPORT"
      completedAt={at}
    />,
  );
  expect(screen.getByRole("region", { name: "高价值机会" })).toBeVisible();
  expect(
    screen.getByText(/研究尚未完成，基金匹配等待研究结果归档/),
  ).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  ["PARTIAL", "ARCHIVED", "部分来源或匹配资料尚待补齐"],
  ["UNAVAILABLE", "UNRECOVERABLE", "不能据此判断没有机会或基金"],
  ["UNAVAILABLE", "TOPIC_ARCHIVE_PENDING", "主题归档或基金匹配正在完成"],
  ["UNAVAILABLE", "ARCHIVING", "主题归档或基金匹配正在完成"],
  ["AVAILABLE", "ARCHIVED", "已归档的匹配结果为零"],
])(
  "distinguishes %s/%s from a confirmed zero-match result",
  async (status, archiveState, message) => {
    vi.mocked(fetch).mockResolvedValueOnce(
      fetchData({
        ...response(),
        status,
        archiveState,
        items: [],
        missingEvidence: [],
        coverage: {
          topicCount: 0,
          matchedTopicCount: 0,
          recommendedFundCount: 0,
        },
      }),
    );
    renderWithSession(<TopicFundRecommendations runId={run} state="READY" />);
    expect(await screen.findByText(new RegExp(message))).toBeVisible();
    if (status !== "AVAILABLE")
      expect(
        screen.queryByText(/已归档的匹配结果为零/),
      ).not.toBeInTheDocument();
  },
);

it.each([
  ["UNRECOVERABLE", []],
  ["ARCHIVED", ["FUND_RECALL_FAILED"]],
  [undefined, []],
])(
  "does not infer zero opportunities from incomplete AVAILABLE matching (%s)",
  async (archiveState, missingEvidence) => {
    vi.mocked(fetch).mockResolvedValueOnce(
      fetchData({
        ...response(),
        status: "AVAILABLE",
        archiveState,
        missingEvidence,
        items: [],
        coverage: {
          topicCount: 0,
          matchedTopicCount: 0,
          recommendedFundCount: 0,
        },
      }),
    );
    renderWithSession(<TopicFundRecommendations runId={run} state="READY" />);
    expect(
      await screen.findByText(
        /基金匹配资料或归档状态尚待补齐，不能据此判断没有基金机会/,
      ),
    ).toBeVisible();
    expect(screen.queryByText(/已归档的匹配结果为零/)).not.toBeInTheDocument();
  },
);

it("retains matched funds during a lifecycle refresh and after the refresh returns 404", async () => {
  let finishRefresh: (value: Response) => void = () => {};
  vi.mocked(fetch)
    .mockResolvedValueOnce(fetchData(response()))
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finishRefresh = resolve;
        }),
    );
  function Lifecycle() {
    const [updated, setUpdated] = useState(false);
    return (
      <>
        <button onClick={() => setUpdated(true)}>更新终态</button>
        <TopicFundRecommendations
          runId={run}
          state={updated ? "FAILED" : "READY"}
          completedAt={updated ? at : null}
        />
      </>
    );
  }
  renderWithSession(<Lifecycle />);
  const originalFund = await screen.findByText(/000001.*机器人主题基金/);
  await userEvent.click(screen.getByRole("button", { name: "更新终态" }));
  expect(screen.getByText(/000001.*机器人主题基金/)).toBe(originalFund);
  expect(
    screen.getByText(/正在刷新基金匹配，继续显示此前已读取的资料/),
  ).toBeVisible();
  await act(async () => {
    finishRefresh(
      new Response(
        JSON.stringify({ error: { code: "NOT_FOUND", message: "missing" } }),
        { status: 404 },
      ),
    );
  });
  expect(
    await screen.findByText(/未找到该研究的基金匹配归档记录/),
  ).toBeVisible();
  expect(screen.getByText(/000001.*机器人主题基金/)).toBe(originalFund);
  expect(
    screen.getByText(/刷新未完成，继续显示此前已读取的匹配资料/),
  ).toBeVisible();
});

it.each(["identity", "session"])(
  "explains an unavailable %s without requesting private data",
  (unavailable) => {
    if (unavailable === "session") session.authenticated = false;
    renderWithSession(
      <TopicFundRecommendations
        runId={unavailable === "identity" ? "invalid" : run}
        state="READY"
      />,
    );
    const region = screen.getByRole("region", { name: "高价值机会" });
    expect(within(region).getByRole("status")).toHaveTextContent(
      unavailable === "session" ? "登录" : "来源尚未确定",
    );
    expect(fetch).not.toHaveBeenCalled();
  },
);
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("connects news opportunities to funds despite no formal report or incomplete risk checks", async () => {
  renderWithSession(ui());
  const region = await screen.findByRole("region", {
    name: "本次研究基金观察",
  });
  expect(await within(region).findByText("机器人订单机会")).toBeVisible();
  expect(within(region).getByText(/000001.*机器人主题基金/)).toBeVisible();
  expect(within(region).getByText("负面风险待核验")).toBeVisible();
  expect(within(region).getByText("存在负面分歧")).toBeVisible();
  expect(within(region).getByText("风险核查已完成")).toBeVisible();
  expect(within(region).getByText("精选池匹配")).toBeVisible();
  expect(within(region).getByText("全量库动态匹配")).toBeVisible();
  expect(within(region).getByText("宽基间接观察")).toBeVisible();
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
    await screen.findByRole("region", { name: "本次研究基金观察" }),
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

it("keeps negative news in risk funds and separates broad exposure from direct recommendations", async () => {
  const data = response();
  Object.assign(data.items[1], {
    topicPolarity: "NEGATIVE_RISK",
    polarityReasons: ["产能过剩风险正在扩大"],
    relatedRiskFunds: data.items[1].recommendedFunds,
    recommendedFunds: [],
  });
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(JSON.stringify({ data, meta: {} })),
  );
  renderWithSession(ui());
  const risk = await screen.findByRole("list", {
    name: "芯片产业机会的风险涉及基金",
  });
  expect(within(risk).getByText(/000002.*芯片精选基金/)).toBeVisible();
  expect(screen.getByText("负面风险线索")).toBeVisible();
  await userEvent.click(
    within(risk.closest("article")!).getByText("研究依据与核查详情"),
  );
  expect(screen.getByText("产能过剩风险正在扩大")).toBeVisible();
  expect(
    screen.queryByRole("list", { name: "芯片产业机会的匹配基金" }),
  ).not.toBeInTheDocument();
  const indirect = screen.getByRole("list", {
    name: "宏观需求改善的间接观察基金",
  });
  expect(within(indirect).getByText(/000003.*宽基指数基金/)).toBeVisible();
  expect(
    screen.queryByRole("list", { name: "宏观需求改善的匹配基金" }),
  ).not.toBeInTheDocument();
});

it("shows association evidence, alternative shares and technical position without purchase promises", async () => {
  const data = response();
  Object.assign(data.items[0], {
    topicPolarity: "POSITIVE_OPPORTUNITY",
    polarityReasons: [],
  });
  Object.assign(data.items[0].recommendedFunds[0], {
    associationLevel: "DIRECT_INDEX",
    associationReason: "核验跟踪指数为中证机器人",
    salesChannel: "OFF_EXCHANGE",
    alternativeShareClasses: [{ fundCode: "000004", name: "机器人主题基金C" }],
    pricePosition: {
      status: "ENTRY_ZONE",
      label: "技术位置接近区间下沿",
      asOf: at,
      reasons: ["净值处于历史区间较低位置"],
      actionable: false,
    },
  });
  vi.mocked(fetch).mockResolvedValueOnce(
    new Response(JSON.stringify({ data, meta: {} })),
  );
  renderWithSession(ui());
  expect(await screen.findByText("指数直配")).toBeVisible();
  expect(screen.getByText(/核验跟踪指数为中证机器人/)).toBeVisible();
  expect(screen.getByText(/场外基金/)).toBeVisible();
  const fund = screen.getByText(/000001.*机器人主题基金/).closest("li")!;
  await userEvent.click(within(fund).getByText("基金资料与风险详情"));
  expect(screen.getByText(/其他份额：000004.*机器人主题基金C/)).toBeVisible();
  expect(screen.getByText(/技术位置接近区间下沿/)).toBeVisible();
  expect(screen.getByText("净值处于历史区间较低位置")).toBeVisible();
  expect(screen.queryByText(/低估|可分批|立即买入/)).not.toBeInTheDocument();
});

it("renders one opportunity headline and loads the full topic catalog only after explicit expansion", async () => {
  renderWithSession(ui());
  await screen.findByText("机器人订单机会");
  expect(
    screen.getAllByRole("heading", { name: "本次研究基金观察" }),
  ).toHaveLength(1);
  expect(
    screen.queryByRole("heading", { name: /值得关注的 AI 投资主题/ }),
  ).not.toBeInTheDocument();
  expect(getResearchTopicRegistry).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText("展开查看完整研究"));
  await userEvent.click(screen.getByText("完整研究主题目录"));
  expect(getResearchTopicRegistry).toHaveBeenCalledOnce();
});

it("shows the server-selected importance and checked source coverage on the same opportunity card", async () => {
  const data = response();
  Object.assign(data.items[0], {
    priority: {
      importance: "HIGH",
      checkedSourceCoveragePercent: 50,
      checkedSourceCount: 1,
      scopeVerified: false,
      researchProgress: "PENDING_REVIEW",
    },
  });
  vi.mocked(fetch).mockResolvedValueOnce(fetchData(data));
  renderWithSession(ui());
  const title = await screen.findByText("机器人订单机会");
  const card = title.closest("article")!;
  expect(within(card).getByText(/高重要性事件关联/)).toBeVisible();
  await userEvent.click(within(card).getByText("研究依据与核查详情"));
  expect(within(card).getByText(/引文核对覆盖50%/)).toBeVisible();
  expect(within(card).getByText(/行业范围关联待核实/)).toBeVisible();
  expect(getResearchTopicRegistry).not.toHaveBeenCalled();
});

function pending(code = "TOPIC_ARCHIVE_PENDING") {
  return {
    ...response(),
    status: "UNAVAILABLE",
    archiveState: "AUDIT_PENDING",
    items: [],
    missingEvidence: [code],
  };
}
function fetchData(data: unknown) {
  return new Response(JSON.stringify({ data, meta: {} }));
}

it("keeps fund summaries visible while report provenance and extended fund evidence are folded", async () => {
  const data = response();
  Object.assign(data.items[0].recommendedFunds[0], {
    alternativeShareClasses: [{ fundCode: "000004", name: "机器人主题基金C" }],
    pricePosition: {
      status: "NEUTRAL",
      label: "中性观察",
      asOf: at,
      reasons: ["OUTSIDE_TECHNICAL_ENTRY_ZONE"],
      actionable: false,
    },
  });
  vi.mocked(fetch).mockResolvedValueOnce(fetchData(data));
  renderWithSession(
    <TopicFundRecommendations runId={run} state="READY" historical />,
  );
  const region = screen.getByRole("region", { name: "高价值机会" });
  const fund = (
    await within(region).findByText(/000001.*机器人主题基金/)
  ).closest("li")!;
  expect(fund).toBeVisible();
  expect(within(fund).getByText(/技术位置：中性观察/)).toBeVisible();
  expect(within(region).getByText(/来源研究ID/)).not.toBeVisible();
  expect(within(fund).getByText(/其他份额/)).not.toBeVisible();
  expect(
    within(fund).getByText("当前净值尚未进入技术观察区间"),
  ).not.toBeVisible();
  await userEvent.click(within(region).getByText("报告来源与匹配说明"));
  expect(within(region).getByText(/来源研究ID/)).toBeVisible();
  await userEvent.click(within(fund).getByText("基金资料与风险详情"));
  expect(within(fund).getByText(/其他份额/)).toBeVisible();
  expect(within(fund).getByText("当前净值尚未进入技术观察区间")).toBeVisible();
});

it("loads a fund's real NAV chart only after expansion and does not invent trade levels", async () => {
  renderWithSession(<TopicFundRecommendations runId={run} state="READY" />);
  const fund = (await screen.findByText(/000001.*机器人主题基金/)).closest(
    "li",
  )!;
  expect(within(fund).queryByRole("img")).not.toBeInTheDocument();
  expect(within(fund).getByText(/匹配归档未提供买卖价位/)).toBeVisible();
  await userEvent.click(within(fund).getByText("查看单位净值走势"));
  expect(
    within(fund).getByRole("img", { name: "000001单位净值走势" }),
  ).toHaveAttribute("data-has-price-plan", "false");
  expect(
    within(fund).queryByText(/建议买入区间|止损参考|止盈目标/),
  ).not.toBeInTheDocument();
});
async function advance(ms = 3000) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

it("polls through archive and fund-recall pending stages and stops after completed matching", async () => {
  vi.useFakeTimers();
  vi.mocked(fetch)
    .mockResolvedValueOnce(fetchData(pending()))
    .mockResolvedValueOnce(fetchData(pending("FUND_RECALL_PENDING")))
    .mockResolvedValue(fetchData(response()));
  renderWithSession(<TopicFundRecommendations runId={run} state="READY" />);
  await advance(5);
  expect(fetch).toHaveBeenCalledOnce();
  await advance();
  expect(fetch).toHaveBeenCalledTimes(2);
  await advance();
  expect(screen.getByText("机器人订单机会")).toBeVisible();
  await advance(60_000);
  expect(fetch).toHaveBeenCalledTimes(3);
});

it("bounds pending polling at ten additional reads and preserves manual recovery", async () => {
  vi.useFakeTimers();
  vi.mocked(fetch).mockImplementation(async () => fetchData(pending()));
  renderWithSession(<TopicFundRecommendations runId={run} state="READY" />);
  await advance(5);
  for (let index = 0; index < 12; index++) await advance();
  expect(fetch).toHaveBeenCalledTimes(11);
  expect(screen.getByText(/自动检查已达上限/)).toBeVisible();
  await advance(60_000);
  expect(fetch).toHaveBeenCalledTimes(11);
  vi.mocked(fetch).mockImplementation(async () => fetchData(response()));
  await act(async () => {
    screen.getByRole("button", { name: "重新检查机会基金匹配" }).click();
  });
  await advance(5);
  expect(screen.getByText("机器人订单机会")).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(12);
});

it.each(["unrecoverable", "identity", "error"])(
  "stops pending polling on %s",
  async (stop) => {
    vi.useFakeTimers();
    vi.mocked(fetch).mockResolvedValueOnce(fetchData(pending()));
    if (stop === "error")
      vi.mocked(fetch).mockRejectedValue(new TypeError("connection failed"));
    else
      vi.mocked(fetch).mockResolvedValue(
        fetchData({
          ...pending(),
          ...(stop === "identity"
            ? { researchId: "another-run" }
            : { archiveState: "UNRECOVERABLE" }),
        }),
      );
    renderWithSession(<TopicFundRecommendations runId={run} state="READY" />);
    await advance(5);
    await advance();
    await advance(60_000);
    expect(fetch).toHaveBeenCalledTimes(2);
  },
);

it("cancels pending polling and in-flight reads when the investment panel collapses", async () => {
  vi.useFakeTimers();
  let signal: AbortSignal | undefined;
  vi.mocked(fetch)
    .mockResolvedValueOnce(fetchData(pending()))
    .mockImplementation((_url, init) => {
      signal = init?.signal as AbortSignal;
      return new Promise<Response>(() => {});
    });
  renderWithSession(ui());
  await advance(5);
  await advance();
  expect(fetch).toHaveBeenCalledTimes(2);
  await act(async () => {
    screen.getByRole("button", { name: /今日投资观察/ }).click();
  });
  expect(signal?.aborted).toBe(true);
  await advance(60_000);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("starts a new polling budget when the same research reaches a different terminal lifecycle", async () => {
  vi.useFakeTimers();
  vi.mocked(fetch).mockImplementation(async () => fetchData(pending()));
  function Lifecycle() {
    const [updated, setUpdated] = useState(false);
    return (
      <>
        <button onClick={() => setUpdated(true)}>更新终态</button>
        <TopicFundRecommendations
          runId={run}
          state={updated ? "FAILED" : "READY"}
          completedAt={updated ? at : null}
        />
      </>
    );
  }
  renderWithSession(<Lifecycle />);
  await advance(5);
  for (let index = 0; index < 10; index++) await advance();
  expect(fetch).toHaveBeenCalledTimes(11);
  await act(async () => {
    screen.getByText("更新终态").click();
  });
  await advance(5);
  expect(fetch).toHaveBeenCalledTimes(12);
  vi.mocked(fetch).mockImplementation(async () => fetchData(response()));
  await advance();
  expect(screen.getByText("机器人订单机会")).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(13);
});

it("aborts the old tenant read and never displays its content after an identity switch", async () => {
  vi.useFakeTimers();
  let previousSignal: AbortSignal | undefined;
  let resolvePrevious: (value: Response) => void = () => {};
  vi.mocked(fetch)
    .mockImplementationOnce((_url, init) => {
      previousSignal = init?.signal as AbortSignal;
      return new Promise<Response>((resolve) => {
        resolvePrevious = resolve;
      });
    })
    .mockImplementation(async () => fetchData(pending()));
  function Identity() {
    const [, rerender] = useState(0);
    return (
      <>
        <button
          onClick={() => {
            session.tenantId = "other-tenant";
            rerender(1);
          }}
        >
          切换身份
        </button>
        <TopicFundRecommendations runId={run} state="READY" />
      </>
    );
  }
  renderWithSession(<Identity />);
  await advance(5);
  await act(async () => {
    screen.getByText("切换身份").click();
  });
  await advance(5);
  expect(previousSignal?.aborted).toBe(true);
  resolvePrevious(fetchData(response()));
  await advance(5);
  expect(screen.queryByText("机器人订单机会")).not.toBeInTheDocument();
});

it("rechecks a cached pending archive on reopening and still permits only ten automatic checks", async () => {
  vi.useFakeTimers();
  vi.mocked(fetch).mockImplementation(async () => fetchData(pending()));
  renderWithSession(ui());
  await advance(5);
  await act(async () => {
    screen.getByRole("button", { name: /今日投资观察/ }).click();
  });
  await act(async () => {
    screen.getByRole("button", { name: /今日投资观察/ }).click();
  });
  await advance(5);
  expect(fetch).toHaveBeenCalledTimes(2);
  for (let index = 0; index < 12; index++) await advance();
  expect(fetch).toHaveBeenCalledTimes(12);
});

it("keeps failed fund recall manual without hiding already available topics", async () => {
  vi.useFakeTimers();
  vi.mocked(fetch).mockImplementation(async () =>
    fetchData({
      ...response(),
      status: "PARTIAL",
      missingEvidence: ["FUND_RECALL_FAILED"],
    }),
  );
  renderWithSession(<TopicFundRecommendations runId={run} state="READY" />);
  await advance(5);
  expect(screen.getByText("机器人订单机会")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "重新检查机会基金匹配" }),
  ).toBeEnabled();
  await advance(60_000);
  expect(fetch).toHaveBeenCalledOnce();
  await act(async () => {
    screen.getByRole("button", { name: "重新检查机会基金匹配" }).click();
  });
  await advance(5);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("refreshes expired completed matching on reopening to update local fund evidence", async () => {
  vi.useFakeTimers();
  renderWithSession(ui());
  await advance(5);
  expect(fetch).toHaveBeenCalledOnce();
  await advance(61_000);
  await act(async () => {
    screen.getByRole("button", { name: /今日投资观察/ }).click();
  });
  await act(async () => {
    screen.getByRole("button", { name: /今日投资观察/ }).click();
  });
  await advance(5);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it.each([
  "recommendedFunds",
  "relatedRiskFunds",
  "observationFunds",
  "indirectFunds",
])(
  "manually rereads pending NAV for completed %s without polling or hiding funds",
  async (group) => {
    vi.useFakeTimers();
    const data = response();
    data.items = [data.items[0]];
    Object.assign(data, {
      status: "AVAILABLE",
      archiveState: "ARCHIVED",
      missingEvidence: [],
      coverage: {
        topicCount: 1,
        matchedTopicCount: 1,
        recommendedFundCount: 1,
      },
    });
    const fund = data.items[0].recommendedFunds[0];
    Object.assign(fund, {
      pricePosition: {
        status: "DATA_PENDING",
        label: "净值资料待同步",
        asOf: null,
        reasons: ["本地净值历史尚待补齐"],
        actionable: false,
      },
    });
    data.items[0].recommendedFunds = [];
    Object.assign(data.items[0], { [group]: [fund] });
    vi.mocked(fetch).mockImplementation(async () => fetchData(data));
    renderWithSession(<TopicFundRecommendations runId={run} state="READY" />);
    await advance(5);

    expect(screen.getByText(/000001.*机器人主题基金/)).toBeVisible();
    expect(
      screen.getByText("净值或技术位置资料待补齐，可稍后重新检查。"),
    ).toBeVisible();
    await advance(60_000);
    expect(fetch).toHaveBeenCalledOnce();

    Object.assign(fund, {
      pricePosition: {
        status: "NEUTRAL",
        label: "技术位置处于区间中部",
        asOf: at,
        reasons: ["本地净值资料已补齐"],
        actionable: false,
      },
    });
    await act(async () => {
      screen.getByRole("button", { name: "重新检查机会基金匹配" }).click();
    });
    await advance(5);

    expect(screen.getByText(/000001.*机器人主题基金/)).toBeVisible();
    expect(screen.getByText(/技术位置处于区间中部/)).toBeVisible();
    expect(
      screen.queryByText("净值或技术位置资料待补齐，可稍后重新检查。"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "重新检查机会基金匹配" }),
    ).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(2);
    for (const [url, options] of vi.mocked(fetch).mock.calls) {
      expect(String(url)).toContain(`/topic-fund-recommendations/${run}`);
      expect(options?.method ?? "GET").toBe("GET");
      expect(options?.body).toBeUndefined();
    }
  },
);

it.each(["MACRO_OBSERVATION", "UNDETERMINED"])(
  "keeps %s funds under observation even when a legacy response misplaces them in recommendations",
  async (topicPolarity) => {
    const data = response();
    data.items = [data.items[0]];
    Object.assign(data.items[0], { topicPolarity });
    vi.mocked(fetch).mockResolvedValueOnce(fetchData(data));
    renderWithSession(ui());
    const list = await screen.findByRole("list", {
      name: "机器人订单机会的相关观察基金",
    });
    expect(within(list).getByText(/000001.*机器人主题基金/)).toBeVisible();
    expect(
      screen.queryByRole("list", { name: "机器人订单机会的匹配基金" }),
    ).not.toBeInTheDocument();
  },
);

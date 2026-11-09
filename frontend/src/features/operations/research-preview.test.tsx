import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import type { ResearchPreview } from "../../api/research-preview";
import ResearchPreviewPage from "./ResearchPreviewPage";
import { renderWithSession } from "../../test/render-with-session";

const runId = "d7810690-ef13-44ad-8591-d3699f3f589a";
const at = "2026-11-07T21:50:28+08:00";
const session = vi.hoisted(() => ({
  tenantId: "tenant-a",
  userId: "owner-a",
  role: "OWNER",
}));
vi.mock("../../app/auth-session", async (original) => ({
  ...(await original<typeof import("../../app/auth-session")>()),
  useAuthSession: () => session,
}));
function fixture(): ResearchPreview {
  const source = {
    id: "source:1",
    title: "原时点机器人报道",
    url: "https://example.org/robot",
    quote: "冻结原文",
    verification: "UNVERIFIED",
    publishedDate: "2026-11-06",
    publishedAt: null,
    retrievedAt: null,
  };
  const direction = {
    key: "robotics_mobility",
    title: "机器人资金约束观察",
    thesis: "融资降温风险仍需保留。",
    signal: "WATCH",
    qualificationStatus: "OBSERVATION_ONLY",
    investmentEligible: false,
    formalEligibilityReasons: ["DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED"],
    sourceIds: [source.id],
    macroCodes: [],
    watchFor: ["等待独立交付证据"],
    risks: [{ summary: "资金链风险", sourceIds: [source.id] }],
    catalysts: [],
    plans: [],
  };
  const report = {
    asOf: at,
    summary: "冻结研究总纲",
    macroCycle: "研究当时信用活化偏弱。",
    valueDirections: [direction],
    actionablePlans: [],
    sources: [source],
    coverage: [],
    events: [],
    upcoming: [],
    energy: [],
  };
  const anchor = {
    methodVersion: "actionable-zones-v3",
    subjectKind: "nav",
    subjectId: "018344",
    fundCode: "018344",
    label: "机器人研究基金",
    priceBasis: "UNIT_NAV",
    unit: "CNY/份",
    asOf: "2026-11-06",
    sourceUri: "https://fund.eastmoney.com/018344.html",
    currentPrice: "1.0563",
    indicators: { ma20: "1.04" },
    tracks: [
      {
        track: "LEFT_VALUE",
        state: "CANDIDATE",
        entryZoneMin: "0.98",
        entryZoneMax: "1.02",
        stopLossPrice: "0.92",
        takeProfitT1: "1.12",
        takeProfitT2: "1.20",
        missingEvidence: [],
        reasons: [],
      },
    ],
  };
  return {
    version: "research-preview-v1",
    mode: "HISTORICAL_SIMULATION",
    runId,
    validatedAt: at,
    generatedAt: "2026-11-08T08:00:00+08:00",
    validatorVersion: "frozen-validator-test",
    readOnly: true,
    investmentEligible: false,
    state: "PARTIAL",
    sourceDigests: {},
    report,
    opportunityContext: {
      state: "PARTIAL",
      items: [],
      dailyResearch: {
        methodVersion: "daily-research-v2",
        state: "READY",
        runId,
        reportRunId: runId,
        completedAt: at,
        reportCompletedAt: at,
        asOf: at,
        report,
        scheduledFor: null,
        startedAt: null,
        nextRunAt: null,
      },
    },
    topicRegistry: {
      items: [
        {
          topicId: "topic:robot",
          key: "robotics_mobility",
          title: "机器人主题",
          summary: "冻结主题观察",
          status: "OBSERVATION_ONLY",
          reasonCode: "OBSERVATION_ONLY",
          reason: "仅供观察",
          impact: "不授予投资资格",
          measures: [],
          pendingTasks: [],
          sourceIds: [source.id],
          match: {
            status: "STANDARD",
            industryIds: ["ROBOT"],
            relatedIndustryIds: [],
            evidence: [],
            registryVersion: "v1",
          },
          investmentEligible: false,
        },
      ],
    },
    funds: [
      {
        key: "nav:018344",
        subjectKind: "nav",
        subjectId: "018344",
        fundCode: "018344",
        market: "FUND_CN",
        label: "机器人研究基金",
        directionKeys: [direction.key],
        investmentEligible: false,
        originalProposals: [
          { directionKey: direction.key, track: "LEFT_VALUE" },
        ],
        anchor,
        analysis: {
          state: "READY",
          score: null,
          indicators: {},
          reasons: [],
          seriesKind: "UNIT_NAV",
          points: [
            { date: "2026-11-05", close: 1.03 },
            { date: "2026-11-06", close: 1.0563 },
          ],
        },
        missingReasons: [],
      },
      {
        key: "nav:013477",
        subjectKind: "nav",
        subjectId: "013477",
        fundCode: "013477",
        market: "FUND_CN",
        label: "原始提及的第二基金",
        directionKeys: [],
        investmentEligible: false,
        originalProposals: [],
        anchor: null,
        analysis: null,
        missingReasons: ["FROZEN_NAV_HISTORY_UNAVAILABLE"],
      },
    ],
    missingReasons: [],
  } as ResearchPreview;
}
beforeEach(() => {
  vi.stubGlobal("IntersectionObserver", undefined);
  session.tenantId = "tenant-a";
  session.userId = "owner-a";
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderPreview(value = fixture()) {
  const calls: string[] = [];
  const fetcher = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), "https://example.test").pathname;
      calls.push(`${init?.method ?? "GET"} ${path}`);
      if (
        path !== `/api/v1/operations/research-preview/${runId}` ||
        (init?.method && init.method !== "GET")
      )
        throw new Error("Unexpected live or write request");
      return new Response(JSON.stringify({ data: value, meta: {} }), {
        status: 200,
      });
    },
  );
  vi.stubGlobal("fetch", fetcher);
  renderWithSession(
    <Routes>
      <Route
        path="/operations/research-preview/:runId"
        element={<ResearchPreviewPage />}
      />
    </Routes>,
    `/operations/research-preview/${runId}`,
  );
  return { calls, fetcher };
}
it("reuses four research workspaces and every original fund without live or write requests", async () => {
  const value = fixture();
  value.funds[0].formalEligibilityReasons = [
    "DIRECTION_INDUSTRY_EXECUTION_UNVERIFIED",
  ];
  const { calls } = renderPreview(value);
  expect(await screen.findByText("frozen-validator-test")).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "历史模拟 · 非实时投资建议" }),
  ).toBeVisible();
  for (const name of ["今日重点", "经济事件", "我的基金", "市场证据"])
    expect(screen.getByRole("tab", { name })).toBeVisible();
  await userEvent.click(screen.getByRole("tab", { name: "我的基金" }));
  const fund = await screen.findByRole("article", { name: "历史基金 018344" });
  expect(fund).toHaveTextContent("资格状态：仅供历史观察");
  expect(fund).toHaveTextContent("资金链风险");
  expect(fund).toHaveTextContent("行业核查尚缺可信执行凭证");
  expect(fund).toHaveTextContent("1.0563");
  expect(fund).toHaveTextContent("研究时确认净值");
  expect(
    within(fund).getByRole("img", { name: "018344单位净值走势" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("article", { name: "历史基金 013477" }),
  ).toHaveTextContent("冻结材料未保存净值历史");
  await userEvent.click(within(fund).getByRole("button", { name: "右侧趋势" }));
  expect(fund).toHaveTextContent("未保存右侧趋势的完整买卖点");
  await userEvent.click(within(fund).getByRole("button", { name: "左侧低吸" }));
  await userEvent.click(within(fund).getByRole("button", { name: "近1年" }));
  await userEvent.click(screen.getByRole("tab", { name: "市场证据" }));
  await userEvent.click(screen.getByText("展开查看完整研究"));
  expect(
    screen.getByRole("region", { name: "机器人资金约束观察" }),
  ).toBeInTheDocument();
  for (const name of ["手动启动研究", "跟踪研究", "设置提醒", "发布报告"])
    expect(screen.getByRole("button", { name })).toBeDisabled();
  expect(calls).toEqual([`GET /api/v1/operations/research-preview/${runId}`]);
});
it("does not reuse a preview response for a different run identity", async () => {
  renderPreview({
    ...fixture(),
    runId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  });
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "历史研究身份不一致",
  );
  expect(
    screen.queryByRole("tab", { name: "我的基金" }),
  ).not.toBeInTheDocument();
});
it("clears the previous private snapshot immediately when the session subject changes", async () => {
  let calls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      calls++;
      if (calls > 1) return new Promise<Response>(() => undefined);
      return new Response(JSON.stringify({ data: fixture(), meta: {} }), {
        status: 200,
      });
    }),
  );
  function SwitchSession() {
    const [other, setOther] = useState(false);
    return (
      <>
        <button
          onClick={() => {
            session.tenantId = "tenant-b";
            session.userId = "owner-b";
            setOther(!other);
          }}
        >
          切换查看身份
        </button>
        <Routes>
          <Route
            path="/operations/research-preview/:runId"
            element={<ResearchPreviewPage />}
          />
        </Routes>
      </>
    );
  }
  renderWithSession(<SwitchSession />, `/operations/research-preview/${runId}`);
  expect((await screen.findAllByText("机器人资金约束观察")).length).toBeGreaterThan(0);
  await userEvent.click(screen.getByRole("button", { name: "切换查看身份" }));
  expect(screen.queryByText("机器人资金约束观察")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("正在读取冻结研究材料");
  expect(calls).toBe(2);
});
it("keeps retained plan cards and their NAV charts frozen without subscribing to live quotes", async () => {
  const value = fixture();
  value.report!.valueDirections![0].plans = [
    {
      ...value.funds[0].anchor!,
      selectedTrack: "LEFT_VALUE",
      track: "LEFT_VALUE",
    },
  ];
  const { calls } = renderPreview(value);
  const plan = await screen.findByRole("article", {
    name: "机器人研究基金买卖计划",
  });
  expect(plan).toHaveTextContent("研究时确认净值");
  expect(
    within(plan).getByRole("button", { name: "跟踪该方向与买卖点 · 018344" }),
  ).toBeDisabled();
  expect(
    within(plan).getByRole("img", { name: "018344单位净值走势" }),
  ).toBeInTheDocument();
  await userEvent.click(
    within(plan).getByRole("button", { name: "冻结全窗口" }),
  );
  expect(calls).toEqual([`GET /api/v1/operations/research-preview/${runId}`]);
});
it("shows the safe backend explanation when frozen material integrity prevents preview", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            error: {
              message: "冻结材料完整性校验失败",
              code: "MATERIALS_INVALID",
            },
          }),
          { status: 409 },
        ),
    ),
  );
  renderWithSession(
    <Routes>
      <Route
        path="/operations/research-preview/:runId"
        element={<ResearchPreviewPage />}
      />
    </Routes>,
    `/operations/research-preview/${runId}`,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "冻结材料完整性校验失败",
  );
  expect(
    screen.queryByRole("tab", { name: "我的基金" }),
  ).not.toBeInTheDocument();
});

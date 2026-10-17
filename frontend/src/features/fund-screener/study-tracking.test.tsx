import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as api from "../../api/study-api";
import StudyTracking from "./components/StudyTracking";
vi.mock("../../api/study-api", () => ({
  getStudyTracks: vi.fn(),
  getStudyDetail: vi.fn(),
  createStudyTrack: vi.fn(),
  updateStudyVersion: vi.fn(),
  setStudyStatus: vi.fn(),
  checkStudy: vi.fn(),
  addStudyNote: vi.fn(),
  getEligibleStudyTrades: vi.fn(),
  linkStudyTrade: vi.fn(),
}));
const snapshot = {
  title: "冻结事件",
  thesis: "油价冲击仍需条件核验",
  transmission: ["成本传导"],
  supports: [],
  counterFactors: ["需求减弱"],
  watchFor: ["后续数据"],
  invalidationConditions: [],
  sources: [],
  origin: {
    originType: "DAILY_EVENT",
    reportRunId: "ready-old",
    eventId: "event-1",
  },
  scope: { indexId: "hs300" },
  createdAt: "2026-10-16T01:00:00Z",
  expiresOn: "2026-11-15",
  conditions: [],
  observations: [],
};
const detail = {
  track: {
    id: "track-1",
    title: "冻结事件",
    enabled: true,
    generation: 3,
    status: "OBSERVING",
    createdAt: snapshot.createdAt,
    updatedAt: snapshot.createdAt,
    unreadCount: 0,
    scope: snapshot.scope,
  },
  version: {
    id: "version-1",
    version: 1,
    generation: 3,
    snapshot,
    createdAt: snapshot.createdAt,
  },
  versions: [],
  checks: [
    {
      id: "check-1",
      versionId: "version-1",
      generation: 3,
      evaluatedAt: snapshot.createdAt,
      result: {
        state: "OBSERVING",
        quality: "UNKNOWN",
        baseline: true,
        hasChange: true,
        stateAsOf: snapshot.createdAt,
        conditionResults: [
          {
            id: "condition-1",
            metric: "PMI",
            result: "UNKNOWN",
            value: null,
            reason: "EVIDENCE_UNAVAILABLE",
          },
        ],
      },
    },
  ],
  notes: [],
  tradeLinks: [],
  history: { limit: 50, offset: 0, hasMore: false },
  capabilities: { canEdit: true, canCheck: true, canLinkTrade: true },
  review: {
    observations: [],
    performance: {
      state: "UNKNOWN",
      netReturn: null,
      excessReturn: null,
      maxDrawdown: null,
      opportunityCost: null,
      reasons: ["缺少完整成本及现金流"],
    },
    causalAttribution: "UNVERIFIED",
  },
} as api.StudyDetail;
function show(path = "/fund-screener", origin?: api.CreateStudy) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <StudyTracking
          actor="actor-a"
          scope={{ indexId: "hs300" }}
          origin={origin}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [detail.track],
    total: 1,
  });
  vi.mocked(api.getStudyDetail).mockResolvedValue(detail);
  vi.mocked(api.createStudyTrack).mockResolvedValue(detail);
  vi.mocked(api.checkStudy).mockResolvedValue(detail);
  vi.mocked(api.updateStudyVersion).mockResolvedValue(detail);
  vi.mocked(api.addStudyNote).mockResolvedValue(detail);
  vi.mocked(api.getEligibleStudyTrades).mockResolvedValue({
    items: [],
    total: 0,
  });
});
it("shows the frozen track prices, date and method from the selected study version", async () => {
  const plan: NonNullable<api.StudySnapshot["actionablePlan"]> = {
    methodVersion: "frozen-zones-v1",
    subjectKind: "index",
    subjectId: "hs300",
    label: "沪深300",
    selectedTrack: "RIGHT_TREND",
    priceBasis: "INDEX_PRICE",
    unit: "points",
    asOf: "2026-10-10",
    sourceUri: "https://example.org/frozen",
    currentPrice: "3750.125",
    indicators: { ma20: 3700 },
    tracks: [
      {
        track: "RIGHT_TREND",
        state: "CANDIDATE",
        priceState: "PARTIAL",
        entryZoneMin: "3700.125",
        entryZoneMax: "3750.125",
        stopLossPrice: null,
        takeProfitT1: null,
        takeProfitT2: null,
        reasons: ["原版本缺少ATR，目标未计算。"],
      },
    ],
  };
  vi.mocked(api.getStudyDetail).mockResolvedValue({
    ...detail,
    version: {
      ...detail.version,
      snapshot: { ...snapshot, actionablePlan: plan },
    },
  });
  show("/fund-screener?studyTrack=track-1");
  const frozen = await screen.findByRole("region", { name: "沪深300双轨计划" });
  expect(frozen).toHaveTextContent("冻结轨道：右侧趋势");
  expect(frozen).toHaveTextContent("3700.125–3750.125 指数点");
  expect(frozen).toHaveTextContent("2026年10月10日");
  expect(frozen).toHaveTextContent("止损：—");
  expect(frozen).toHaveTextContent("frozen-zones-v1");
  expect(within(frozen).queryByRole("button")).not.toBeInTheDocument();
});
it("only freezes the explicit READY report origin after a user action, with the default thirty-day horizon", async () => {
  show("/fund-screener", {
    originType: "DAILY_EVENT",
    reportRunId: "ready-old",
    eventId: "event-1",
  });
  const user = userEvent.setup();
  expect(api.createStudyTrack).not.toHaveBeenCalled();
  await user.click(screen.getByText("新建研究"));
  await user.click(screen.getByRole("button", { name: "冻结研究并跟踪" }));
  expect(api.createStudyTrack).toHaveBeenCalledWith({
    originType: "DAILY_EVENT",
    reportRunId: "ready-old",
    eventId: "event-1",
    horizonDays: 30,
  });
  expect(await screen.findByText("油价冲击仍需条件核验")).toBeInTheDocument();
});
it("shows frozen version and UNKNOWN without calling it falsified; checking is explicit and generation guarded", async () => {
  show("/fund-screener?studyTrack=track-1");
  expect(await screen.findByText("油价冲击仍需条件核验")).toBeInTheDocument();
  expect(screen.getByText(/无法核验，保留此前已核实状态/)).toBeInTheDocument();
  expect(api.checkStudy).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "核对已存证据" }));
  expect(api.checkStudy).toHaveBeenCalledWith("track-1", 3);
  expect(screen.getByText(/净收益：—/)).toBeInTheDocument();
});
it("starts with state, frozen evidence and history while deferring the long record and trade forms", async () => {
  show("/fund-screener?studyTrack=track-1");
  expect(await screen.findByText("油价冲击仍需条件核验")).toBeVisible();
  expect(screen.getByRole("region", { name: "研究时间线" })).toBeVisible();
  expect(screen.getByRole("region", { name: "证据复盘" })).toBeVisible();
  expect(screen.getByRole("button", { name: "核对已存证据" })).toBeVisible();
  const disclosure = screen.getByText("记录与复盘操作").closest("details")!;
  expect(disclosure).not.toHaveAttribute("open");
  expect(
    screen
      .getByRole("region", { name: "证据复盘" })
      .compareDocumentPosition(disclosure) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(screen.getByLabelText("决策原因")).not.toBeVisible();
  expect(screen.getByLabelText("复盘原因")).not.toBeVisible();
  expect(
    screen.getByRole("button", { name: "关联真实成交" }),
  ).not.toBeVisible();
  expect(api.getEligibleStudyTrades).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText("记录与复盘操作"));
  expect(screen.getByLabelText("决策原因")).toBeVisible();
  expect(screen.getByLabelText("复盘原因")).toBeVisible();
  expect(screen.getByRole("button", { name: "关联真实成交" })).toBeVisible();
});

it("reads condition operators, effects and subjects in Chinese and keeps unknown codes neutral without rewriting the frozen source", async () => {
  const frozen = {
    ...snapshot,
    benchmarkIndexId: "hs300",
    conditions: [
      {
        metric: "INDEX_MA200_GAP",
        subjectKey: "hs300",
        operator: "GT",
        threshold: "3",
        unit: "%",
        effect: "STRENGTHEN",
      },
      {
        metric: "INDEX_MOMENTUM63",
        subjectKey: "a500",
        operator: "GTE",
        threshold: 0,
        unit: "%",
        effect: "WEAKEN",
      },
      {
        metric: "INDEX_PE",
        subjectKey: "zz500",
        operator: "LT",
        threshold: 20,
        unit: "倍",
        effect: "TRIGGER",
      },
      {
        metric: "PMI",
        operator: "LTE",
        threshold: 50,
        unit: "指数点",
        effect: "INVALIDATE",
      },
      {
        metric: "FUTURE_METRIC",
        subjectKey: "future-index",
        operator: "FUTURE_OPERATOR",
        threshold: 1,
        unit: "%",
        effect: "FUTURE_EFFECT",
      },
    ],
  };
  const original = JSON.stringify(frozen);
  vi.mocked(api.getStudyDetail).mockResolvedValue({
    ...detail,
    version: { ...detail.version, snapshot: frozen },
  } as api.StudyDetail);
  show("/fund-screener?studyTrack=track-1");
  const conditions = await screen.findByRole("region", {
    name: "研究验证条件",
  });
  expect(conditions).toHaveTextContent(
    "指数偏离MA200 沪深300 大于 3 % · 研究依据增强",
  );
  expect(conditions).toHaveTextContent(
    "指数63期动量 中证A500 不低于 0 % · 研究依据减弱",
  );
  expect(conditions).toHaveTextContent(
    "指数滚动PE 中证500 小于 20 倍 · 预设条件触发",
  );
  expect(conditions).toHaveTextContent(
    "制造业PMI 不高于 50 指数点 · 预设失效条件触发",
  );
  expect(conditions).toHaveTextContent(
    "其他待核查指标 关联标的待核查 比较方式待核查 1 % · 条件影响待核查",
  );
  expect(conditions).not.toHaveTextContent(
    /STRENGTHEN|WEAKEN|TRIGGER|INVALIDATE|hs300|a500|zz500|FUTURE_/,
  );
  expect(screen.getByText(/基准：沪深300/)).toBeVisible();
  const raw = screen.getByText("原始条件依据").closest("details")!;
  expect(raw).not.toHaveAttribute("open");
  expect(
    within(raw).getByText(/INDEX_MA200_GAP hs300 GT 3 % · STRENGTHEN/),
  ).not.toBeVisible();
  await userEvent.click(within(raw).getByText("原始条件依据"));
  expect(
    within(raw).getByText(/INDEX_MA200_GAP hs300 GT 3 % · STRENGTHEN/),
  ).toBeVisible();
  expect(JSON.stringify(frozen)).toBe(original);
});
it("creates a new version with catalog conditions and percentage-point threshold without overwriting the original", async () => {
  show("/fund-screener?studyTrack=track-1");
  await screen.findByText("油价冲击仍需条件核验");
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "修订研究条件" }));
  await user.selectOptions(
    screen.getByLabelText("核对指标"),
    "INDEX_MA200_GAP",
  );
  await user.selectOptions(screen.getByLabelText("条件指数"), "hs300");
  await user.type(screen.getByLabelText("阈值"), "3");
  await user.type(screen.getByLabelText("修订原因"), "明确核对条件");
  await user.click(screen.getByRole("button", { name: "保存新版本" }));
  expect(api.updateStudyVersion).toHaveBeenCalledWith(
    "track-1",
    expect.objectContaining({
      expectedGeneration: 3,
      reason: "明确核对条件",
      conditions: [
        expect.objectContaining({
          metric: "INDEX_MA200_GAP",
          subjectKey: "hs300",
          threshold: "3",
          unit: "%",
        }),
      ],
    }),
  );
});
it("keeps plans separate from fills and only offers eligible confirmed trades", async () => {
  show("/fund-screener?studyTrack=track-1");
  await screen.findByText("油价冲击仍需条件核验");
  const user = userEvent.setup();
  await user.click(screen.getByText("记录与复盘操作"));
  await user.selectOptions(screen.getByLabelText("决策记录"), "PLAN_BUY");
  await user.type(screen.getByLabelText("决策原因"), "仅记录计划");
  await user.click(screen.getByRole("button", { name: "保存决策记录" }));
  expect(api.addStudyNote).toHaveBeenCalledWith(
    "track-1",
    expect.objectContaining({
      kind: "DECISION",
      action: "PLAN_BUY",
      expectedGeneration: 3,
      reason: "仅记录计划",
    }),
  );
  await user.click(screen.getByRole("button", { name: "关联真实成交" }));
  expect(
    await screen.findByText("暂无有权限且身份匹配的已确认成交。"),
  ).toBeInTheDocument();
  expect(
    within(screen.getByRole("region", { name: "真实成交关联" })).queryByRole(
      "button",
      { name: "确认关联成交" },
    ),
  ).not.toBeInTheDocument();
  expect(api.linkStudyTrade).not.toHaveBeenCalled();
});

it("clears private form drafts when selecting another study identity", async () => {
  vi.mocked(api.getStudyTracks).mockResolvedValue({
    items: [
      detail.track,
      { ...detail.track, id: "track-2", title: "另一研究" },
    ],
    total: 2,
  });
  show("/fund-screener?studyTrack=track-1");
  await screen.findByText("油价冲击仍需条件核验");
  const user = userEvent.setup();
  await user.click(screen.getByText("记录与复盘操作"));
  await user.type(screen.getByLabelText("决策原因"), "当前主题私有草稿");
  await user.click(screen.getByRole("button", { name: /另一研究/ }));
  await screen.findByText("油价冲击仍需条件核验");
  expect(
    screen.getByText("记录与复盘操作").closest("details"),
  ).not.toHaveAttribute("open");
  await user.click(screen.getByText("记录与复盘操作"));
  expect(screen.getByLabelText("决策原因")).toHaveValue("");
});

it("retains frozen phase, scenarios and source quotations and labels current-version comparisons while reading old snapshots", async () => {
  const original = {
    ...detail.version,
    id: "version-old",
    version: 1,
    snapshot: {
      ...snapshot,
      phase: "ANNOUNCED",
      importance: "HIGH",
      unknowns: ["原始事件规模尚未确认"],
      affectedDirectionIds: ["gold:gold"],
      scenarios: [
        {
          condition: "实际供给收缩",
          impact: "成本压力可能上升",
          counter: "需求回落抵消",
        },
      ],
      sources: [
        {
          id: "s1",
          url: "https://example.org/fact",
          title: "原始声明",
          quote: "原始短引文",
          publishedDate: "2026-10-14",
          retrievedAt: "2026-10-16T01:00:00Z",
        },
      ],
    },
  };
  vi.mocked(api.getStudyDetail).mockResolvedValue({
    ...detail,
    version: { ...detail.version, version: 2 },
    versions: [original],
    notes: [
      {
        id: "lifecycle",
        versionId: "version-1",
        kind: "LIFECYCLE",
        payload: { reason: "用户停止主题", enabled: false },
        createdAt: snapshot.createdAt,
      },
    ],
  } as unknown as api.StudyDetail);
  show("/fund-screener?studyTrack=track-1");
  await screen.findByText("油价冲击仍需条件核验");
  await userEvent.click(screen.getByRole("button", { name: /查看版本1/ }));
  expect(screen.getByText(/事件阶段：已宣布/)).toBeInTheDocument();
  expect(screen.getByText("实际供给收缩")).toBeInTheDocument();
  expect(screen.getByText("成本压力可能上升")).toBeInTheDocument();
  expect(screen.getByText("需求回落抵消")).toBeInTheDocument();
  expect(screen.getByText("原始短引文")).toBeInTheDocument();
  expect(screen.getByText("原始事件规模尚未确认")).toBeInTheDocument();
  expect(screen.getByText(/下方事实对照基于当前版本2/)).toBeInTheDocument();
  expect(screen.getByText(/主题生命周期记录/)).toBeInTheDocument();
});

it("does not leak an old actor's late create result into the new actor", async () => {
  let complete!: (value: api.StudyDetail) => void;
  vi.mocked(api.createStudyTrack).mockImplementation(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrap = ({ actor }: { actor: string }) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <StudyTracking actor={actor} scope={{ indexId: "hs300" }} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const view = render(<Wrap actor="a" />);
  await userEvent.click(screen.getByText("新建研究"));
  await userEvent.click(screen.getByRole("button", { name: "冻结研究并跟踪" }));
  view.rerender(<Wrap actor="b" />);
  await import("@testing-library/react").then(({ act }) =>
    act(async () => {
      complete(detail);
      await Promise.resolve();
    }),
  );
  expect(screen.queryByText("油价冲击仍需条件核验")).not.toBeInTheDocument();
  expect(api.getStudyDetail).not.toHaveBeenCalled();
});

it("links only an explicitly selected eligible confirmed fill with its real day and price", async () => {
  const fill = {
    id: "fill-1",
    fillId: "fill-1",
    instrumentId: "fund-1",
    instrumentName: "真实基金",
    side: "BUY",
    quantity: "100",
    unitPrice: "1.234",
    fee: "0.5",
    currency: "CNY",
    tradeDate: "2026-10-16",
    status: "CONFIRMED",
    executionTimePrecision: "DAY",
  } as api.EligibleStudyTrade;
  vi.mocked(api.getEligibleStudyTrades).mockResolvedValue({
    items: [fill],
    total: 1,
  });
  vi.mocked(api.linkStudyTrade).mockResolvedValue(detail);
  show("/fund-screener?studyTrack=track-1");
  await screen.findByText("油价冲击仍需条件核验");
  const user = userEvent.setup();
  await user.click(screen.getByText("记录与复盘操作"));
  await user.click(screen.getByRole("button", { name: "关联真实成交" }));
  await screen.findByRole("option", {
    name: /2026-10-16.*真实基金.*100.*1.234.*CNY.*0.5.*CNY/,
  });
  expect(api.linkStudyTrade).not.toHaveBeenCalled();
  await user.selectOptions(screen.getByLabelText("已确认成交"), "fill-1");
  await user.type(screen.getByLabelText("关联原因"), "核对真实执行");
  await user.click(screen.getByRole("button", { name: "确认关联成交" }));
  expect(api.linkStudyTrade).toHaveBeenCalledWith("track-1", {
    expectedGeneration: 3,
    fillId: "fill-1",
    versionId: "version-1",
    reason: "核对真实执行",
  });
});

it("keeps a selected historical snapshot stable across pagination and binds its review to that exact version", async () => {
  const old = {
    ...detail.version,
    id: "old",
    version: 1,
    snapshot: { ...snapshot, thesis: "原始V1假设" },
  };
  vi.mocked(api.getStudyDetail).mockImplementation(
    async (_id, _signal, offset) => ({
      ...detail,
      version: {
        ...detail.version,
        id: "new",
        version: 2,
        snapshot: { ...snapshot, thesis: "当前V2假设" },
      },
      versions: offset ? [] : [old],
      history: { limit: 50, offset: offset ?? 0, hasMore: !offset },
    }),
  );
  show("/fund-screener?studyTrack=track-1");
  await screen.findByText("当前V2假设");
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /查看版本1/ }));
  await user.click(screen.getByText("记录与复盘操作"));
  await user.type(screen.getByLabelText("复盘原因"), "核对历史版本");
  await user.click(screen.getByRole("button", { name: "保存人工复盘" }));
  expect(api.addStudyNote).toHaveBeenCalledWith(
    "track-1",
    expect.objectContaining({
      versionId: "old",
      kind: "REVIEW",
      expectedGeneration: 3,
    }),
  );
  await user.click(screen.getByRole("button", { name: "更早的记录" }));
  await waitFor(() =>
    expect(api.getStudyDetail).toHaveBeenLastCalledWith(
      "track-1",
      expect.anything(),
      50,
    ),
  );
  expect(screen.getByText("原始V1假设")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "返回当前版本2" }));
  expect(screen.getByText("当前V2假设")).toBeInTheDocument();
});

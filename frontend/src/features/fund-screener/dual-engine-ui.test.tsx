import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type {
  ActionableZoneResult,
  DailyResearch,
  OpportunityContext,
  ValueDirection,
} from "../../api/investment";
import ValueDirections from "./components/ValueDirections";
import DailyResearchReport from "./components/DailyResearchReport";
import MacroContextStrip from "./components/MacroContextStrip";
import ActionablePlan from "./components/ActionablePlan";

const plan = (code = "162411") =>
  ({
    methodVersion: "actionable-zones-v3",
    subjectKind: "nav",
    subjectId: code,
    fundCode: code,
    label: `赛道基金${code}`,
    priceBasis: "UNIT_NAV",
    priceTimeBasis: "LATEST_DISCLOSED_NAV",
    unit: "CNY/份",
    asOf: "2026-10-16",
    sourceUri: `https://fund.eastmoney.com/${code}.html`,
    currentPrice: "1.2345",
    indicators: { ma20: "1.2200" },
    selectedTrack: "LEFT_VALUE",
    tracks: [
      {
        track: "LEFT_VALUE",
        state: "CANDIDATE",
        entryZoneMin: "1.2000",
        entryZoneMax: "1.2500",
        stopLossPrice: "1.1000",
        takeProfitT1: "1.3500",
        takeProfitT2: "1.4500",
        priceState: "COMPLETE",
        reasons: [],
      },
    ],
  }) as ActionableZoneResult;
const direction = (index = 0): ValueDirection => ({
  key: `sector_${index}`,
  title: `赛道机会${index + 1}`,
  thesis: "产业催化与真实净值位置形成共同研究依据。",
  signal: "LEFT_VALUE",
  sourceIds: ["media", "official"],
  catalysts: [
    {
      summary: "同产业链回购与重组催化",
      sourceIds: ["missing", "bad", "media", "media", "official"],
    },
  ],
  risks: [
    { summary: "供给压力与事件不确定性", sourceIds: ["official", "media"] },
  ],
  macroCodes: [],
  watchFor: ["下次披露与需求兑现"],
  plans: [plan(String(162411 + index * 2)), plan(String(162412 + index * 2))],
});
const daily = (): DailyResearch => ({
  methodVersion: "daily-research-v1",
  state: "READY",
  runId: "run-1",
  reportRunId: "report-1",
  scheduledFor: null,
  startedAt: null,
  completedAt: null,
  asOf: "2026-10-19T01:00:00Z",
  nextRunAt: null,
  report: {
    summary: "宏观与产业研究",
    coverage: [],
    sources: [
      {
        id: "media",
        title: "行业媒体电报",
        url: "https://www.cls.cn/detail/123456",
        verification: "UNVERIFIED",
      },
      {
        id: "official",
        title: "公司原始公告",
        url: "https://example.org/official",
        verification: "SOURCE_CHECKED",
      },
      { id: "bad", title: "无效链接", url: "javascript:void(0)" },
    ].map((row) => ({
      quote: "原始引用不改写",
      publishedDate: "2026-10-18",
      publishedAt: null,
      retrievedAt: "2026-10-19T00:00:00Z",
      verification: "UNVERIFIED",
      ...row,
    })),
    events: ["media", "official"].map((id) => ({
      id,
      title: id === "media" ? "先到的行业快讯" : "后到的原文事件",
      summary: "事件事实摘要",
      phase: "ANNOUNCED",
      importance: "HIGH",
      sourceIds: [id],
      channels: [],
      affectedDirectionIds: [],
      scenarios: [],
      watchFor: [],
      unknowns: [],
      marketReaction: "UNVERIFIED",
      reasoningType: "MODEL_CONDITIONAL",
    })),
    upcoming: [
      {
        id: "calendar",
        title: "产业会议",
        phase: "PLANNED",
        scheduledDate: "2026-10-20",
        scheduledAt: null,
        sourceIds: [],
        affectedDirectionIds: [],
        watchFor: [],
      },
    ],
    energy: [],
    valueDirections: [direction()],
  },
});

it("numbers only valid distinct catalyst and risk sources and preserves their original payload", () => {
  const data = daily();
  const original = JSON.stringify(data);
  render(<ValueDirections data={data} />);
  for (const name of ["机会催化", "关键风险"]) {
    const region = screen.getByRole("region", { name });
    const links = within(region).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual([
      "[来源1]",
      "[来源2]",
    ]);
    expect(links[0].parentElement).toHaveClass("op-inline-sources");
    expect(region).not.toHaveTextContent("原文原文");
    expect(region.querySelector('[href^="javascript:"]')).toBeNull();
  }
  expect(JSON.stringify(data)).toBe(original);
});

it("shows eight directions with two complete plans each and resets opened details for a new report", async () => {
  const data = daily();
  data.report!.valueDirections = Array.from({ length: 8 }, (_, index) =>
    direction(index),
  );
  const { rerender } = render(<ValueDirections data={data} />);
  expect(screen.getAllByText("建议买入区间")).toHaveLength(16);
  expect(screen.getAllByText("止盈目标二")).toHaveLength(16);
  const summaries = screen.getAllByText("综合依据与后续观察");
  for (const summary of summaries)
    expect(summary.closest("details")).not.toHaveAttribute("open");
  await userEvent.click(summaries[0]);
  await userEvent.click(summaries[3]);
  expect(summaries[0].closest("details")).toHaveAttribute("open");
  expect(summaries[3].closest("details")).toHaveAttribute("open");
  await userEvent.click(screen.getAllByText("查看技术指标与算法依据")[0]);
  rerender(<ValueDirections data={{ ...data, reportRunId: "report-2" }} />);
  for (const details of document.querySelectorAll("details"))
    expect(details).not.toHaveAttribute("open");
  await userEvent.click(screen.getAllByText("综合依据与后续观察")[0]);
  expect(
    screen.getAllByText("综合依据与后续观察")[0].closest("details"),
  ).toHaveAttribute("open");
});

it("shows disclosed QDII prices with publication delay context on compact and full plans", () => {
  const data = daily();
  const original = JSON.stringify(data);
  render(
    <>
      <ValueDirections data={data} />
      <ActionablePlan result={plan()} />
    </>,
  );
  const warning = /最新披露净值参考，存在跨市场发布延迟；不是当日估值/;
  expect(
    screen.getAllByText(warning).filter((row) => !row.closest("details")),
  ).toHaveLength(3);
  expect(screen.getAllByText(/1\.2000–1\.2500/)).toHaveLength(3);
  expect(screen.queryByText(/等待真实价格依据/)).not.toBeInTheDocument();
  expect(JSON.stringify(data)).toBe(original);
});

it("keeps equally important media events in report order and reads source states in Chinese", async () => {
  const data = daily();
  const original = JSON.stringify(data);
  render(<DailyResearchReport data={data} />);
  expect(
    within(screen.getByRole("list", { name: "重要事件" }))
      .getAllByRole("heading")
      .map((row) => row.textContent),
  ).toEqual(["先到的行业快讯", "后到的原文事件"]);
  await userEvent.click(screen.getByText("阅读完整日报"));
  expect(screen.getByText("媒体快讯")).toBeVisible();
  expect(screen.getByText("原文已核对")).toBeVisible();
  expect(document.body).not.toHaveTextContent(/UNVERIFIED|SOURCE_CHECKED/);
  expect(screen.getAllByText("原始引用不改写")).toHaveLength(2);
  expect(JSON.stringify(data)).toBe(original);
});

it("resets the report calendar, full report, execution and official disclosures while preserving independent toggles", async () => {
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  const data: OpportunityContext = {
    state: "READY",
    items: [],
    dailyResearch: daily(),
    events: [
      {
        id: "other",
        title: "官方事实",
        category: "DATA_RELEASE",
        sourceType: "OFFICIAL_FACT",
        publisher: "官方机构",
        sourceUri: null,
        publishedAt: null,
        retrievedAt: null,
        summary: "独立事实",
        directionIds: [],
      },
    ],
  };
  const show = (context: OpportunityContext) => (
    <MemoryRouter>
      <MacroContextStrip data={context} error={false} />
    </MemoryRouter>
  );
  const { rerender } = render(show(data));
  const labels = [
    /接下来关注什么/,
    "阅读完整日报",
    "执行与报告状态",
    "官方发布与其他观察",
  ];
  for (const label of labels) {
    const summary = screen.getByText(label);
    expect(summary.closest("details")).not.toHaveAttribute("open");
    await userEvent.click(summary);
  }
  for (const label of labels)
    expect(screen.getByText(label).closest("details")).toHaveAttribute("open");
  rerender(
    show({
      ...data,
      dailyResearch: { ...data.dailyResearch!, reportRunId: "report-2" },
    }),
  );
  for (const label of labels)
    expect(screen.getByText(label).closest("details")).not.toHaveAttribute(
      "open",
    );
  const errors = [...consoleError.mock.calls];
  consoleError.mockRestore();
  expect(errors).toEqual([]);
});

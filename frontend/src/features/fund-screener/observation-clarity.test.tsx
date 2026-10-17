import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import type { OpportunityContext } from "../../api/investment";
import MacroContextStrip from "./components/MacroContextStrip";

const context = {
  state: "PARTIAL",
  items: [],
  researchOverview: {
    methodVersion: "research-overview-v2",
    summary: "沪深300趋势条件未通过，继续等待趋势和盈利证据。",
    highlights: [
      {
        id: "trend:hs300",
        kind: "INDEX_TREND",
        title: "沪深300趋势风险观察",
        summary: "趋势型策略条件尚不满足。",
        impact: "不代表预测后续下跌。",
        tone: "ORANGE",
        targetTab: "direction",
        indexId: "hs300",
        sourceIds: [],
        directionIds: [],
        condition: "FAILED",
        resultType: "INCONCLUSIVE",
        nextValidation: ["收盘价恢复MA200且63期动量为正"],
        counterConditions: ["趋势改善仍需盈利确认"],
      },
    ],
  },
  dailyResearch: {
    state: "NOT_RUN",
    scheduleState: "DUE_NOT_DISPATCHED",
    todayScheduledFor: "2026-10-17T08:30:00+08:00",
    report: null,
    nextRunAt: "2026-10-18T08:30:00+08:00",
  },
  researchBoard: {
    methodVersion: "research-board-v2",
    items: [],
    summaries: [],
    direction: {
      label: "A股宽基",
      summary: "方向长篇重复",
      supports: [],
      counterFactors: [],
      recommendedChecks: [],
      itemIds: [],
    },
    dataStatus: {
      itemIds: [],
      groups: [],
      sourceResults: {},
      methodVersions: [],
      collectedAt: null,
      nextCheckAt: null,
    },
  },
} as unknown as OpportunityContext;
function State() {
  return <output aria-label="URL">{useLocation().search}</output>;
}
function show(path = "/fund-screener", onIndexSelect?: (id: string) => void) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MacroContextStrip
        data={context}
        error={false}
        onIndexSelect={onIndexSelect}
      />
      <State />
      <div id="op-research-targets" tabIndex={-1}>
        现有指数基金工作台
      </div>
    </MemoryRouter>,
  );
}

it("keeps a collapsed observation to a short judgment and changes its visible control when opened", async () => {
  show();
  const user = userEvent.setup();
  expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("region", { name: "研究机会" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/暂无有效日报/)).toBeInTheDocument();
  expect(screen.getByText(/今日到期.*尚未登记派发/)).toBeInTheDocument();
  const button = screen.getByRole("button", { name: /展开研究/ });
  expect(button).toHaveAttribute("aria-expanded", "false");
  await user.click(button);
  expect(screen.getByRole("button", { name: /收起研究/ })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  expect(screen.getAllByRole("tab")).toHaveLength(3);
  expect(screen.getByRole("tab", { name: "今日重点" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
});
it("maps legacy direction links into useful observations and sends asset research to the existing workbench", async () => {
  const choose = vi.fn();
  show(
    "/fund-screener?researchOpen=1&researchTab=direction&fundMarket=SSE&macroMonths=60",
    choose,
  );
  expect(screen.getByRole("tab", { name: "今日重点" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.queryByText("方向长篇重复")).not.toBeInTheDocument();
  expect(screen.getByText("尚无符合完整条件的基金候选")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /研究沪深300/ }));
  expect(choose).toHaveBeenCalledWith("hs300");
  expect(screen.getByLabelText("URL")).toHaveTextContent("fundMarket=SSE");
});
it("opens one observation inline with its declared next condition, without another direction screen", async () => {
  show("/fund-screener?researchOpen=1");
  expect(screen.getByText("收盘价恢复MA200且63期动量为正")).toBeVisible();
  await userEvent.click(screen.getByText("沪深300趋势风险观察"));
  expect(screen.getByText("收盘价恢复MA200且63期动量为正")).toBeVisible();
  expect(
    screen.queryByRole("tab", { name: "投资方向与基金" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("keeps unknown trend evidence visibly unverified instead of giving it a positive research tone", () => {
  const unknown = {
    ...context,
    researchOverview: {
      ...context.researchOverview!,
      highlights: [
        {
          ...context.researchOverview!.highlights[0],
          condition: "UNKNOWN",
          tone: "GRAY",
        },
      ],
    },
  } as OpportunityContext;
  render(
    <MemoryRouter initialEntries={["/fund-screener?researchOpen=1"]}>
      <MacroContextStrip data={unknown} error={false} />
    </MemoryRouter>,
  );
  expect(screen.getByText("无法核验")).toBeInTheDocument();
});
it("opens evidence from a standalone legacy data flag while preserving selection", () => {
  show("/fund-screener?researchOpen=1&researchData=1&index=a500");
  expect(screen.getByRole("tab", { name: "市场证据" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByLabelText("URL")).toHaveTextContent("index=a500");
});
it("keeps a verified official upcoming release available when there is no daily report", () => {
  const calendar = {
    ...context,
    researchOverview: {
      ...context.researchOverview!,
      highlights: [
        {
          ...context.researchOverview!.highlights[0],
          id: "official-schedule",
          kind: "UPCOMING",
          title: "制造业PMI发布计划",
          asOf: "2026-10-31T09:30:00+08:00",
        },
      ],
    },
  } as OpportunityContext;
  render(
    <MemoryRouter initialEntries={["/fund-screener?researchOpen=1"]}>
      <MacroContextStrip data={calendar} error={false} />
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("region", { name: "官方发布日历" }),
  ).toHaveTextContent("制造业PMI发布计划");
});

it("keeps auxiliary official releases available without filling the primary focus with their full records", async () => {
  const official = {
    ...context,
    events: [
      {
        id: "official:1",
        title: "官方月度发布",
        summary: "完整官方统计正文",
        sourceType: "OFFICIAL_FACT",
        publisher: "统计机构",
        sourceUri: "https://www.stats.gov.cn/example",
        publishedAt: null,
      },
    ],
  } as unknown as OpportunityContext;
  render(
    <MemoryRouter initialEntries={["/fund-screener?researchOpen=1"]}>
      <MacroContextStrip data={official} error={false} />
    </MemoryRouter>,
  );
  expect(screen.getByText("完整官方统计正文")).not.toBeVisible();
  await userEvent.click(screen.getByText("官方发布与其他观察"));
  expect(screen.getByText("完整官方统计正文")).toBeVisible();
});

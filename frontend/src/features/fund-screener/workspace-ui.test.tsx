import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { renderWithSession } from "../../test/render-with-session";
import type { OpportunityContext } from "../../api/investment";
import MacroContextStrip from "./components/MacroContextStrip";

vi.mock("./components/EconomicEvents", () => ({
  EconomicEvents: () => <p>事件独立阅读</p>,
  EconomicEventTeasers: ({
    onOpenEvent,
  }: {
    onOpenEvent: (id: string) => void;
  }) => (
    <button onClick={() => onOpenEvent("historical:event")}>
      事件简讯入口
    </button>
  ),
}));
vi.mock("./components/TopicFundRecommendations", () => ({
  default: ({ presentation }: { presentation?: string }) => (
    <p>机会呈现：{presentation}</p>
  ),
}));
vi.mock("./components/MacroEvidencePanel", () => ({
  default: () => <p>市场证据数据</p>,
}));
vi.mock("./components/Phase2Evidence", () => ({ default: () => null }));
const data = {
  state: "READY",
  items: [],
  events: [],
  dailyResearch: {
    state: "UNCERTAIN",
    runId: "new-timeout",
    reportRunId: "old-valid",
    reportCompletedAt: "2026-11-06T08:00:00+08:00",
    report: {
      summary: "上一份有效判断。",
      events: [],
      sources: [],
      coverage: [],
      energy: [],
      upcoming: [],
    },
  },
} as unknown as OpportunityContext;
function Ui({
  loading = false,
  context = data,
}: {
  loading?: boolean;
  context?: OpportunityContext;
}) {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  return (
    <>
      <output aria-label="当前地址">{params.toString()}</output>
      <button onClick={() => navigate(-1)}>返回上一页</button>
      <MacroContextStrip
        data={loading ? undefined : context}
        loading={loading}
        error={false}
        renderMyFunds={(renderReview) => (
          <section aria-label="我的私人基金">
            私人基金独立加载{renderReview?.()}
          </section>
        )}
        renderStudyTracking={() => <p>原研究复盘</p>}
        renderPersonalReminders={(onViewFunds) => (
          <button onClick={onViewFunds}>查看我的基金提醒</button>
        )}
      />
    </>
  );
}
it("has four ordered workspaces and keeps the personal workspace readable during report loading", async () => {
  renderWithSession(
    <Ui loading />,
    "/fund-screener?researchTab=tracking&studyTrack=original",
  );
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
    "今日重点",
    "经济事件",
    "我的基金",
    "市场证据",
  ]);
  expect(screen.getByRole("region", { name: "我的私人基金" })).toBeVisible();
  expect(screen.getByText("原研究复盘")).toBeVisible();
  expect(screen.getByText("正在加载展示报告…")).toBeVisible();
});
it("keeps Today concise and navigates event teasers without losing the private deep link", async () => {
  renderWithSession(
    <Ui />,
    "/fund-screener?studyTrack=original&researchTab=events&fund=123456",
  );
  expect(screen.getByRole("region", { name: "今日宏观总纲" })).toBeVisible();
  expect(screen.queryByText("展开查看完整研究")).not.toBeInTheDocument();
  expect(screen.queryByText("私人基金独立加载")).not.toBeInTheDocument();
  await userEvent.click(screen.getByText("事件简讯入口"));
  expect(screen.getByRole("tab", { name: "经济事件" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByText("事件独立阅读")).toBeVisible();
  expect(screen.getByLabelText("当前地址")).toHaveTextContent(
    "economicEvent=historical%3Aevent",
  );
  expect(screen.getByLabelText("当前地址")).toHaveTextContent(
    "studyTrack=original",
  );
  await userEvent.click(screen.getByText("返回上一页"));
  await waitFor(() =>
    expect(screen.getByRole("tab", { name: "今日重点" })).toHaveAttribute(
      "aria-selected",
      "true",
    ),
  );
});
it("moves complete research to evidence and supports four-tab keyboard navigation", async () => {
  renderWithSession(<Ui />);
  const first = screen.getByRole("tab", { name: "今日重点" });
  first.focus();
  await userEvent.keyboard("{End}");
  expect(screen.getByRole("tab", { name: "市场证据" })).toHaveFocus();
  expect(
    screen.getByText("展开查看完整研究").closest("details"),
  ).not.toHaveAttribute("open");
  await userEvent.keyboard("{Home}{ArrowRight}");
  expect(screen.getByRole("tab", { name: "经济事件" })).toHaveFocus();
});
it("keeps execution diagnostics secondary to the displayed report", () => {
  renderWithSession(<Ui />);
  expect(
    screen.getByText("执行与调度详情").closest("details"),
  ).not.toHaveAttribute("open");
  expect(
    screen.getByRole("group", { name: "本次执行状态", hidden: true }),
  ).not.toBeVisible();
  expect(screen.getByLabelText("展示报告来源")).toBeVisible();
});

it.each(["", "&reportRunId=old-valid"])(
  "reveals the saved report on an initial source hash link %s",
  async (identity) => {
    renderWithSession(
      <Ui />,
      `/fund-screener?researchTab=macro&researchOpen=1${identity}#latest-successful-daily-report`,
    );
    await waitFor(() =>
      expect(
        screen.getByRole("region", { name: "每日事件研究" }),
      ).toBeVisible(),
    );
    expect(
      screen.getByRole("group", { name: "展示报告的买卖计划" }),
    ).toHaveAttribute("data-report-run-id", "old-valid");
  },
);

it("waits for the linked report to load and preserves the user's collapse on a same-route refresh", async () => {
  function AsyncReport() {
    const [loaded, setLoaded] = useState(false);
    const [revision, setRevision] = useState(0);
    return (
      <>
        <button onClick={() => setLoaded(true)}>报告加载完成</button>
        <button onClick={() => setRevision(revision + 1)}>报告数据刷新</button>
        <Ui loading={!loaded} context={{ ...data, items: [...data.items] }} />
      </>
    );
  }
  renderWithSession(
    <AsyncReport />,
    "/fund-screener?researchTab=macro&researchOpen=1&reportRunId=old-valid#latest-successful-daily-report",
  );
  expect(screen.queryByText("展开查看完整研究")).not.toBeInTheDocument();
  await userEvent.click(screen.getByText("报告加载完成"));
  await waitFor(() =>
    expect(screen.getByRole("region", { name: "每日事件研究" })).toBeVisible(),
  );
  await userEvent.click(screen.getByText("展开查看完整研究"));
  expect(
    screen.getByText("展开查看完整研究").closest("details"),
  ).not.toHaveAttribute("open");
  await userEvent.click(screen.getByText("报告数据刷新"));
  expect(
    screen.getByText("展开查看完整研究").closest("details"),
  ).not.toHaveAttribute("open");
  expect(
    screen.getByRole("region", { name: "每日事件研究" }),
  ).not.toBeVisible();
});

it.each([data, { state: "READY", items: [] } as OpportunityContext])(
  "explains that an explicitly linked report is unavailable instead of revealing another report",
  async (context) => {
    renderWithSession(
      <Ui context={context} />,
      "/fund-screener?researchTab=macro&researchOpen=1&reportRunId=not-loaded#latest-successful-daily-report",
    );
    expect(
      await screen.findByText(
        "所选报告出处未加载；当前内容不能作为这份报告的出处。",
      ),
    ).toBeVisible();
    expect(
      screen.getByText("展开查看完整研究").closest("details"),
    ).not.toHaveAttribute("open");
    expect(
      screen.getByRole("region", { name: "每日事件研究" }),
    ).not.toBeVisible();
  },
);

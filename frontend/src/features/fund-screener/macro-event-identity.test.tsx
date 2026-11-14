import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import type { OpportunityContext } from "../../api/investment";
import MacroContextStrip from "./components/MacroContextStrip";

const event = {
  id: "shared-id",
  title: "同名官方公告",
  category: "DATA_RELEASE",
  sourceType: "OFFICIAL_FACT" as const,
  publisher: "官方机构",
  sourceUri: "https://example.org/release",
  publishedAt: "2026-10-16T01:00:00Z",
  retrievedAt: "2026-10-16T02:00:00Z",
  summary: "宏观重复正文",
  directionIds: [],
};
const data: OpportunityContext = {
  state: "READY",
  items: [],
  events: [event, { ...event, id: "independent-id", summary: "独立事实正文" }],
  dailyResearch: {
    methodVersion: "daily-v1",
    state: "READY",
    runId: "saved-report",
    scheduledFor: null,
    startedAt: null,
    completedAt: "2026-10-16T02:00:00Z",
    asOf: "2026-10-16T02:00:00Z",
    nextRunAt: null,
    report: {
      summary: "已保存日报",
      coverage: [],
      sources: [],
      events: [
        {
          id: "shared-id",
          title: "同名官方公告",
          summary: "日报主项正文",
          phase: "ANNOUNCED",
          importance: "HIGH",
          sourceIds: [],
          channels: [],
          affectedDirectionIds: [],
          scenarios: [],
          watchFor: [],
          unknowns: [],
          marketReaction: "UNVERIFIED",
          reasoningType: "MODEL_CONDITIONAL",
        },
      ],
      upcoming: [],
      energy: [],
    },
  },
};

function show(context: OpportunityContext) {
  render(
    <MemoryRouter initialEntries={["/?researchOpen=1&researchTab=macro"]}>
      <MacroContextStrip data={context} error={false} />
    </MemoryRouter>,
  );
}

it("does not hide an independent official fact just because a report reuses its ID", async () => {
  show(data);
  await userEvent.click(screen.getByText("展开查看完整研究"));
  await userEvent.click(screen.getByText("官方发布与其他观察"));
  const official = screen.getByRole("region", { name: "官方关键事件" });
  expect(within(official).getByText("宏观重复正文")).toBeVisible();
  expect(within(official).getByText("独立事实正文")).toBeInTheDocument();
  expect(
    within(screen.getByRole("list", { name: "重要事件" })).getByText(
      "事实：日报主项正文",
    ),
  ).toBeInTheDocument();
  expect(
    within(screen.getByRole("list", { name: "重要事件" })).getAllByText(
      "同名官方公告",
    ),
  ).toHaveLength(1);
  expect(within(official).getAllByText("同名官方公告")).toHaveLength(2);
});

it("retains the official fact when no saved daily report contains that ID", async () => {
  show({ ...data, dailyResearch: undefined });
  await userEvent.click(screen.getByText("展开查看完整研究"));
  await userEvent.click(screen.getByText("官方发布与其他观察"));
  expect(screen.getByText("宏观重复正文")).toBeInTheDocument();
  expect(screen.getByText("独立事实正文")).toBeInTheDocument();
});

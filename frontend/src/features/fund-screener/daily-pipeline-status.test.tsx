import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { DailyResearch, OpportunityContext } from "../../api/investment";
import DailyResearchReport, {
  DailyResearchStatus,
} from "./components/DailyResearchReport";
import MacroContextStrip from "./components/MacroContextStrip";
import ValueDirections from "./components/ValueDirections";

const daily = (overrides: Partial<DailyResearch> = {}): DailyResearch => ({
  methodVersion: "daily-research-v1",
  state: "NOT_RUN",
  scheduleState: "NOT_DUE",
  todayScheduledFor: "2026-10-29T23:00:00Z",
  runId: null,
  scheduledFor: null,
  startedAt: null,
  completedAt: null,
  asOf: null,
  nextRunAt: "2026-11-01T23:00:00Z",
  report: null,
  ...overrides,
});

it("puts the actual pipeline trigger after the readable status in Beijing time", () => {
  render(
    <DailyResearchStatus
      data={daily({
        state: "RUNNING",
        startedAt: "2026-10-29T23:03:00Z",
        pipeline: {
          id: "pipeline-today",
          state: "RUNNING",
          stage: "COLLECTING",
          startedAt: "2026-10-29T23:01:00Z",
        },
      })}
    />,
  );
  expect(
    screen.getByText(/正在采集公共数据（触发：2026年10月30日 07:01 北京时间）/),
  ).toBeVisible();
  expect(screen.queryByText(/07:03|登记|派发/)).not.toBeInTheDocument();
});

it("labels a scheduled trigger as planned rather than an actual start", () => {
  render(<DailyResearchStatus data={daily()} />);
  expect(
    screen.getByText(/今日尚未启动（计划触发：2026年10月30日 07:00 北京时间）/),
  ).toBeVisible();
  expect(screen.queryByText(/（触发：/)).not.toBeInTheDocument();
});

it("does not invent a trigger from an old report's scheduled timestamp", () => {
  render(
    <DailyResearchStatus
      data={daily({
        todayScheduledFor: null,
        scheduledFor: "2026-10-27T00:30:00Z",
      })}
    />,
  );
  expect(screen.getByText("今日尚未启动")).toBeVisible();
  expect(screen.queryByText(/触发：|08:30/)).not.toBeInTheDocument();
});

it.each(["2026-10-30T23:00:00Z", "2026-10-31T23:00:00Z"])(
  "shows Monday as the next planned trigger instead of the inactive weekend slot %s",
  (todayScheduledFor) => {
    render(
      <DailyResearchStatus
        data={daily({
          scheduleState: "NON_WORKDAY",
          todayScheduledFor,
          nextRunAt: "2026-11-01T23:00:00Z",
        })}
      />,
    );
    expect(
      screen.getByText(
        "非工作日，不安排自动研究（下次计划触发：2026年11月2日 07:00 北京时间）",
      ),
    ).toBeVisible();
    expect(screen.queryByText(/10月31日|11月1日/)).not.toBeInTheDocument();
  },
);

it("does not claim a weekend trigger when the next schedule is unknown", () => {
  render(
    <DailyResearchStatus
      data={daily({
        scheduleState: "NON_WORKDAY",
        todayScheduledFor: "2026-10-30T23:00:00Z",
        nextRunAt: null,
      })}
    />,
  );
  expect(screen.getByText("非工作日，不安排自动研究")).toBeVisible();
  expect(screen.queryByText(/触发：/)).not.toBeInTheDocument();
});

it("does not advertise a pending trigger while automatic research is disabled", () => {
  render(
    <DailyResearchStatus
      data={daily({ state: "DISABLED", scheduleState: "DISABLED" })}
    />,
  );
  expect(screen.getByText("自动研究已关闭")).toBeVisible();
  expect(screen.queryByText(/触发：/)).not.toBeInTheDocument();
});

it.each([
  ["WAITING_SOURCES", "等待公共数据"],
  ["VALIDATING_INPUT", "正在检查研究输入"],
  ["MODEL_RUNNING", "AI研究中"],
  ["VERIFYING_OUTPUT", "正在核验报告"],
  ["PERSISTING", "正在保存报告"],
])("distinguishes the active %s pipeline phase", (stage, expected) => {
  render(
    <DailyResearchStatus
      data={daily({
        state: "RUNNING",
        pipeline: { id: "p", state: "RUNNING", stage },
      })}
    />,
  );
  expect(screen.getByText(new RegExp(expected))).toBeVisible();
  expect(screen.queryByText(/研究成功|已生成/)).not.toBeInTheDocument();
});

it.each([
  ["FAILED", "研究失败"],
  ["UNCERTAIN", "执行异常 · 待核实"],
  ["UNKNOWN", "执行状态未知"],
])(
  "keeps %s distinct and falls back safely for legacy responses",
  (state, expected) => {
    render(<DailyResearchStatus state={state} />);
    expect(screen.getByText(expected)).toBeVisible();
  },
);

it.each([
  ["NON_WORKDAY", "非工作日，不安排自动研究"],
  ["MISSED", "今日已错过启动时段"],
  ["DISABLED", "自动研究已关闭"],
])("explains an inactive %s schedule", (scheduleState, expected) => {
  render(<DailyResearchStatus state="NOT_RUN" scheduleState={scheduleState} />);
  expect(screen.getByText(expected)).toBeVisible();
});

it("shows success only after READY and never appends a dispatch-slot status", () => {
  render(
    <DailyResearchStatus state="READY" scheduleState="DISPATCHED" hasReport />,
  );
  expect(screen.getByText("研究成功 · 报告已生成")).toBeVisible();
  expect(screen.queryByText(/登记|派发/)).not.toBeInTheDocument();
});

it("uses the same status for the compact header and full execution details while keeping an old report", async () => {
  const data = daily({
    state: "FAILED",
    scheduleState: "DISPATCHED",
    startedAt: "2026-10-29T23:01:00Z",
    completedAt: "2026-10-29T23:06:00Z",
    errorClass: "SourceQualityBlocked",
    reportRunId: "yesterday",
    reportCompletedAt: "2026-10-29T00:00:00Z",
    report: {
      asOf: "2026-10-29T00:00:00Z",
      summary: "保留昨天的研究依据",
      coverage: [],
      sources: [],
      events: [],
      upcoming: [],
      energy: [],
    },
  });
  const view = render(
    <MemoryRouter>
      <MacroContextStrip
        data={
          {
            state: "READY",
            items: [],
            dailyResearch: data,
          } as OpportunityContext
        }
        error={false}
      />
    </MemoryRouter>,
  );
  const label = "研究失败（触发：2026年10月30日 07:01 北京时间）";
  expect(screen.getAllByText(label)).toHaveLength(2);
  expect(screen.getByText(/当前显示历史报告及原始日期/)).toBeVisible();
  await userEvent.click(screen.getByText("执行与报告状态"));
  const details = screen.getByText("执行与报告状态").closest("details")!;
  expect(within(details).getByText(/结束：2026年10月30日 07:06/)).toBeVisible();
  expect(within(details).getByText("SourceQualityBlocked")).toBeVisible();
  expect(within(details).getByText(/报告.*2026年10月29日/)).toBeVisible();
  view.unmount();
});

it("shows a compact execution-stage timeline without exposing unsafe errors", async () => {
  render(
    <DailyResearchReport
      data={daily({
        state: "RUNNING",
        pipeline: {
          id: "p",
          state: "RUNNING",
          stage: "VERIFYING_OUTPUT",
          startedAt: "2026-10-29T23:00:00Z",
          updatedAt: "2026-10-29T23:10:00Z",
          errorClass: "token=secret",
        },
      })}
    />,
  );
  await userEvent.click(screen.getByText("执行与报告状态"));
  const stages = screen.getByRole("list", { name: "研究执行阶段" });
  expect(within(stages).getByText("核验报告")).toHaveAttribute(
    "aria-current",
    "step",
  );
  expect(screen.queryByText(/token=secret/)).not.toBeInTheDocument();
  expect(screen.getByText(/自动研究计划以服务配置为准/)).toBeVisible();
  expect(
    screen.queryByText(/工作日07:00|周一至周五07:00/),
  ).not.toBeInTheDocument();
});

it.each([
  ["DAILY", "07:00", "每天07:00启动AI研究（北京时间）"],
  ["DAILY", "08:15", "每天08:15启动AI研究（北京时间）"],
  ["WEEKDAYS", "08:15", "周一至周五08:15启动AI研究（北京时间）"],
] as const)(
  "uses the configured %s %s schedule in both report details and value directions",
  async (mode, time, label) => {
    const data = daily({
      schedule: {
        mode,
        time,
        timezone: "Asia/Shanghai",
        startWindowMinutes: 120,
      },
      report: {
        summary: "研究摘要",
        coverage: [],
        sources: [],
        events: [],
        upcoming: [],
        energy: [],
      },
    });
    render(
      <>
        <DailyResearchReport data={data} />
        <ValueDirections data={data} />
      </>,
    );
    await userEvent.click(screen.getByText("执行与报告状态"));
    expect(
      screen.getAllByText(new RegExp(label.replace(/[()]/g, "\\$&"))),
    ).toHaveLength(2);
    expect(
      screen.queryByText(/工作日07:00|自动研究计划以服务配置为准/),
    ).not.toBeInTheDocument();
  },
);

it("keeps a Saturday planned trigger when the service is configured for every day", async () => {
  render(
    <DailyResearchReport
      data={daily({
        schedule: {
          mode: "DAILY",
          time: "07:00",
          timezone: "Asia/Shanghai",
          startWindowMinutes: 120,
        },
        todayScheduledFor: "2026-10-30T23:00:00Z",
        nextRunAt: "2026-10-31T23:00:00Z",
      })}
    />,
  );
  await userEvent.click(screen.getByText("执行与报告状态"));
  expect(
    screen.getByText("今日尚未启动（计划触发：2026年10月31日 07:00 北京时间）"),
  ).toBeVisible();
  expect(screen.getByText(/每天07:00启动AI研究（北京时间）/)).toBeVisible();
  expect(
    screen.getByText(/下次计划：2026年11月1日 07:00 北京时间/),
  ).toBeVisible();
  expect(screen.queryByText(/非工作日/)).not.toBeInTheDocument();
});

it("explains a configured weekday schedule and uses the service's next Monday trigger", async () => {
  render(
    <DailyResearchReport
      data={daily({
        schedule: {
          mode: "WEEKDAYS",
          time: "08:15",
          timezone: "Asia/Shanghai",
          startWindowMinutes: 120,
        },
        scheduleState: "NON_WORKDAY",
        todayScheduledFor: null,
        nextRunAt: "2026-11-02T00:15:00Z",
      })}
    />,
  );
  await userEvent.click(screen.getByText("执行与报告状态"));
  expect(
    screen.getByText(
      "非工作日，不安排自动研究（下次计划触发：2026年11月2日 08:15 北京时间）",
    ),
  ).toBeVisible();
  expect(
    screen.getByText(/周一至周五08:15启动AI研究（北京时间）/),
  ).toBeVisible();
});

it("keeps the plan neutral in a legacy report with no schedule metadata", () => {
  render(
    <ValueDirections
      data={daily({
        report: {
          summary: "历史摘要",
          coverage: [],
          sources: [],
          events: [],
          upcoming: [],
          energy: [],
        },
      })}
    />,
  );
  expect(screen.getByText(/自动研究计划以服务配置为准/)).toBeVisible();
  expect(
    screen.queryByText(/每天07:00|工作日07:00|周一至周五/),
  ).not.toBeInTheDocument();
});

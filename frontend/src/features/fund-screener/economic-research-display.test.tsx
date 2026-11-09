import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { DailyResearch } from "../../api/investment";
import DailyResearchReport, {
  DailyResearchStatus,
} from "./components/DailyResearchReport";

const current = "e08fdff1-b25e-4c5b-9b82-97387925408b";
const previous = "c670f1fc-a8c9-47d8-b319-e5eb589592bb";
const daily = (): DailyResearch => ({
  methodVersion: "daily-research-v1",
  state: "FAILED",
  runId: current,
  reportRunId: previous,
  scheduledFor: null,
  startedAt: null,
  completedAt: null,
  asOf: null,
  nextRunAt: null,
  report: {
    summary: "旧READY报告保留",
    coverage: [],
    sources: [],
    events: [],
    upcoming: [],
    energy: [],
  },
  economicResearch: {
    version: "economic-research-delivery-v1",
    researchId: current,
    status: "PARTIAL",
    asOf: "2026-11-05T08:00:00+08:00",
    validatedAt: "2026-11-05T08:00:00+08:00",
    recordedAt: "2026-11-05T08:01:00+08:00",
    archiveState: "ARCHIVED",
    archivePending: false,
    acceptedContentComplete: true,
    actionable: false,
    coverage: {
      discoveredTopicCount: 64,
      evidenceBackedTopicCount: 2,
      pendingVerificationTopicCount: 62,
      evidenceBackedTopicRatioPercent: 3.1,
      qualifiedFormalDirectionCount: 0,
      investmentQualificationStatus: "NO_QUALIFIED_OPPORTUNITIES",
    },
    observations: {
      sources: [
        {
          id: "today",
          title: "本次原文",
          url: "https://example.test/today",
          quote: "当期订单变化。",
          verification: "SOURCE_CHECKED",
        },
      ],
      industryCycleReviews: [],
      valueDirections: [],
      upcoming: [],
      events: [
        {
          id: "today-event",
          title: "今日独立经济新闻",
          summary: "保留经济事实与条件推演。",
          sourceIds: ["today"],
          channels: ["条件成立时可能影响交付。"],
          unknowns: ["实际传导尚未兑现。"],
        },
      ],
    },
  },
});

it("leads with today's successful report time and suppresses residual failure diagnostics", () => {
  const data = daily();
  Object.assign(data, { state: "READY", reportRunId: current, completedAt: "2026-11-09T08:12:00+08:00", reportCompletedAt: "2026-11-09T08:12:00+08:00", failureReasonCode: "TIMEOUT" });
  render(<DailyResearchStatus data={data} />);
  expect(screen.getByText("✅ 今日投研已完成（08:12）")).toBeVisible();
  expect(screen.queryByText(/正式报告未通过|执行超时/)).toBeNull();
});

it("explains the automatic-only 23-hour dispatch cooldown separately from economic delivery", () => {
  const data = daily();
  Object.assign(data, {
    automaticResearchCooldown: {
      active: true,
      hours: 23,
      lastAiDispatchAt: "2026-11-05T08:00:00+08:00",
      automaticEligibleAt: "2026-11-06T07:00:00+08:00",
    },
  });
  render(<DailyResearchStatus data={data} />);
  expect(screen.getByText(/自动研究冷却中（23小时）/)).toBeVisible();
  expect(screen.getByText(/仅限制自动研究/)).toBeVisible();
  expect(
    screen.getByText("研究观察资料已更新，部分来源仍在补充"),
  ).toBeVisible();
});

it("does not claim an expired or disabled automatic schedule is cooling", () => {
  const data = daily();
  Object.assign(data, {
    automaticResearchCooldown: {
      active: false,
      hours: 23,
      lastAiDispatchAt: "2026-11-04T08:00:00+08:00",
      automaticEligibleAt: "2026-11-05T07:00:00+08:00",
    },
  });
  const view = render(<DailyResearchStatus data={data} />);
  expect(screen.queryByText(/自动研究冷却中/)).not.toBeInTheDocument();
  data.scheduleState = "DISABLED";
  data.automaticResearchCooldown!.active = true;
  view.rerender(<DailyResearchStatus data={data} />);
  expect(screen.queryByText(/自动研究冷却中/)).not.toBeInTheDocument();
});

it("makes today's economic delivery the primary status while keeping formal investment failure separate", () => {
  const data = daily();
  data.economicResearch!.status = "AVAILABLE";
  Object.assign(data.economicResearch!.coverage!, {
    evidenceBackedTopicCount: 64,
    pendingVerificationTopicCount: 0,
    evidenceBackedTopicRatioPercent: 100,
  });
  render(<DailyResearchStatus data={data} />);
  expect(screen.getByText("今日经济研究已有可阅读成果")).toBeVisible();
  expect(
    screen
      .getByText("今日经济研究已有可阅读成果")
      .closest(".op-research-status"),
  ).toHaveAttribute("data-state", "AVAILABLE");
  expect(screen.getByText(/正式投资报告：正式报告未通过/)).toBeVisible();
  expect(data.state).toBe("FAILED");
});

it("keeps scheduling status when neither the economic projection nor daily response has a current run", () => {
  const data = daily();
  data.state = "NOT_RUN";
  data.runId = null;
  data.economicResearch!.researchId = null;
  data.economicResearch!.status = "UNKNOWN";
  render(<DailyResearchStatus data={data} />);
  expect(screen.getByText("今日尚未启动")).toBeVisible();
  expect(
    screen.queryByText("今日经济研究交付状态未知"),
  ).not.toBeInTheDocument();
});

it("puts the current read-only information overview before a retained READY report without trading actions or inflated completeness", async () => {
  const data = daily();
  render(<DailyResearchReport data={data} />);
  const overview = screen.getByRole("region", { name: "今日信息总览" });
  const content = within(overview);
  expect(
    content.getByText(
      "今日经济研究已有可阅读成果：64个发现主题，其中2个具备已核对来源支持，其余62个仍需核实；暂无合格机会",
    ),
  ).toBeVisible();
  expect(content.getByText("今日独立经济新闻")).toBeVisible();
  expect(content.getByText("实际传导尚未兑现。")).toBeVisible();
  await userEvent.click(content.getByText("查看本项来源依据"));
  expect(content.getAllByRole("link", { name: "本次原文" })[0]).toHaveAttribute(
    "href",
    "https://example.test/today",
  );
  const old = screen.getByText(/当前显示历史报告及原始日期/);
  expect(
    overview.compareDocumentPosition(old) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(content.queryByText(/研究已完成/)).not.toBeInTheDocument();
  expect(
    content.queryByRole("button", { name: /买入|跟踪|推送|重跑/ }),
  ).not.toBeInTheDocument();
});

it.each([
  ["PARTIAL", "研究观察资料已更新，部分来源仍在补充"],
  ["PENDING", "今日经济研究资料待归档"],
  ["UNAVAILABLE", "今日经济研究暂无可阅读成果"],
  ["UNKNOWN", "今日经济研究交付状态未知"],
] as const)(
  "keeps %s economic delivery distinct from formal investment failure",
  (status, label) => {
    const data = daily();
    data.economicResearch!.status = status;
    render(<DailyResearchStatus data={data} />);
    expect(screen.getByText(label)).toBeVisible();
    expect(
      screen.getByText(label).closest(".op-research-status"),
    ).toHaveAttribute("data-state", status);
    expect(screen.getByText(/正式投资报告：正式报告未通过/)).toBeVisible();
    expect(screen.queryByText(/经济研究已完成/)).not.toBeInTheDocument();
  },
);

it("does not borrow an unrelated economic delivery or render pending observations as current news", () => {
  const data = daily();
  data.economicResearch!.researchId = previous;
  const first = render(<DailyResearchReport data={data} />);
  expect(
    screen.queryByRole("region", { name: "今日信息总览" }),
  ).not.toBeInTheDocument();
  first.unmount();
  const pending = daily();
  pending.economicResearch!.status = "PENDING";
  render(<DailyResearchReport data={pending} />);
  expect(screen.queryByText("今日独立经济新闻")).not.toBeInTheDocument();
});

it("preserves scheduling and pre-model execution states when both economic and current research IDs are null", () => {
  const data = daily();
  data.runId = null;
  data.economicResearch!.researchId = null;
  data.economicResearch!.status = "UNKNOWN";
  data.state = "NOT_RUN";
  const view = render(<DailyResearchStatus data={data} />);
  expect(screen.getByText("今日尚未启动")).toBeVisible();
  data.scheduleState = "DISABLED";
  view.rerender(<DailyResearchStatus data={data} />);
  expect(screen.getByText("自动调度已关闭")).toBeVisible();
  data.scheduleState = "DISPATCHED";
  data.state = "RUNNING";
  data.pipeline = {
    id: "pre-model-pipeline",
    state: "RUNNING",
    stage: "COLLECTING",
    researchRunId: null,
  };
  view.rerender(<DailyResearchStatus data={data} />);
  expect(screen.getByText("正在采集公共数据")).toBeVisible();
  expect(
    screen.queryByText("今日经济研究交付状态未知"),
  ).not.toBeInTheDocument();
});

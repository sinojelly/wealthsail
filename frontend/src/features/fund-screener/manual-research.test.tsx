import { act, screen, waitFor, within } from "@testing-library/react";
import { useQuery } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import type { DailyResearch } from "../../api/investment";
import * as api from "../../api/daily-research-debug";
import { HttpError } from "../../api/client";
import { renderWithSession } from "../../test/render-with-session";
import DailyResearchRefresh from "./components/DailyResearchRefresh";
import DailyResearchReport, {
  DailyResearchStatus,
} from "./components/DailyResearchReport";

vi.mock("../../api/daily-research-debug", () => ({
  getResearchRefresh: vi.fn(),
  startResearchRefresh: vi.fn(),
  getResearchRefreshStatus: vi.fn(),
}));

const previousRunId = "84e066c3-2750-4bbb-a655-3b64d04eb5dc";
const currentRunId = "1949d499-dbbe-4eca-8833-310ead646a62";
const request = (
  status: api.DebugResearchRequest["status"] = "QUEUED",
): api.DebugResearchRequest => ({
  id: "manual-1",
  status,
  requestMode: "MANUAL",
  requestedAt: new Date().toISOString(),
  startedAt: null,
  completedAt: null,
  batchId: null,
  reportRunId: null,
  errorClass: null,
});
const report = (manual = false): DailyResearch => ({
  methodVersion: "daily-research-v1",
  state: "READY",
  runMode: manual ? "MANUAL" : "SCHEDULED",
  reportRunMode: manual ? "MANUAL" : "SCHEDULED",
  runId: manual ? currentRunId : previousRunId,
  reportRunId: manual ? currentRunId : previousRunId,
  scheduledFor: null,
  startedAt: null,
  completedAt: null,
  asOf: null,
  nextRunAt: null,
  report: {
    summary: manual ? "新手动研究摘要" : "保留的自动研究摘要",
    coverage: [],
    sources: [],
    events: [],
    upcoming: [],
    energy: [],
  },
});
function ResearchPage({
  load,
  onReady,
}: {
  load: () => Promise<DailyResearch>;
  onReady?: () => void;
}) {
  const data = useQuery({
    queryKey: ["owner-a", "opportunity-context"],
    queryFn: load,
    staleTime: Infinity,
  });
  return (
    <>
      <DailyResearchRefresh actor="owner-a" onReady={onReady} />
      <DailyResearchReport data={data.data} />
    </>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: null,
  });
  vi.mocked(api.startResearchRefresh).mockResolvedValue(request());
  vi.mocked(api.getResearchRefreshStatus).mockResolvedValue(request("RUNNING"));
});

it("recovers an unacknowledged command across a page remount and clears it only after acknowledgment", async () => {
  vi.mocked(api.startResearchRefresh).mockRejectedValueOnce(
    new Error("network timeout"),
  );
  const first = renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  await userEvent.click(
    await screen.findByRole("button", { name: "手动启动研究" }),
  );
  expect(
    await screen.findByRole("button", { name: "重试本次提交" }),
  ).toBeEnabled();
  const originalKey = vi.mocked(api.startResearchRefresh).mock.calls[0][0];
  first.unmount();

  vi.mocked(api.startResearchRefresh).mockResolvedValueOnce({
    ...request("READY"),
    reportRunId: currentRunId,
  });
  const second = renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  await userEvent.click(
    await screen.findByRole("button", { name: "重试本次提交" }),
  );
  await screen.findByText(/手动研究已完成/);
  expect(vi.mocked(api.startResearchRefresh).mock.calls[1][0]).toBe(
    originalKey,
  );
  second.unmount();

  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  await userEvent.click(
    await screen.findByRole("button", { name: "手动启动研究" }),
  );
  expect(vi.mocked(api.startResearchRefresh).mock.calls[2][0]).not.toBe(
    originalKey,
  );
});

it("does not let an unmounted request's late acknowledgment clear a newer unacknowledged command", async () => {
  let acknowledgeOld!: (value: api.DebugResearchRequest) => void;
  const oldResponse = new Promise<api.DebugResearchRequest>((resolve) => {
    acknowledgeOld = resolve;
  });
  let failNew!: (reason: Error) => void;
  const newResponse = new Promise<api.DebugResearchRequest>(
    (_resolve, reject) => {
      failNew = reject;
    },
  );
  const ready = { ...request("READY"), reportRunId: currentRunId };
  vi.mocked(api.startResearchRefresh)
    .mockReturnValueOnce(oldResponse)
    .mockResolvedValueOnce(ready)
    .mockReturnValueOnce(newResponse);
  const first = renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  await userEvent.click(
    await screen.findByRole("button", { name: "手动启动研究" }),
  );
  const oldKey = vi.mocked(api.startResearchRefresh).mock.calls[0][0];
  first.unmount();

  const second = renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  await userEvent.click(
    await screen.findByRole("button", { name: "重试本次提交" }),
  );
  await screen.findByText(/手动研究已完成/);
  expect(vi.mocked(api.startResearchRefresh).mock.calls[1][0]).toBe(oldKey);
  await userEvent.click(screen.getByRole("button", { name: "手动启动研究" }));
  const newKey = vi.mocked(api.startResearchRefresh).mock.calls[2][0];
  expect(newKey).not.toBe(oldKey);
  await act(async () => {
    acknowledgeOld(ready);
    await oldResponse;
  });
  expect(
    sessionStorage.getItem("wealthsail:manual-research:command:owner-a"),
  ).toBe(newKey);

  await act(async () => {
    failNew(new Error("network timeout"));
  });
  second.unmount();
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  await userEvent.click(
    await screen.findByRole("button", { name: "重试本次提交" }),
  );
  expect(vi.mocked(api.startResearchRefresh).mock.calls[3][0]).toBe(newKey);
});

it("keeps unacknowledged command identities separate for different actors", async () => {
  vi.mocked(api.startResearchRefresh).mockRejectedValueOnce(
    new Error("network timeout"),
  );
  const first = renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  await userEvent.click(
    await screen.findByRole("button", { name: "手动启动研究" }),
  );
  await screen.findByRole("button", { name: "重试本次提交" });
  const firstKey = vi.mocked(api.startResearchRefresh).mock.calls[0][0];
  first.unmount();
  renderWithSession(<DailyResearchRefresh actor="owner-b" />);
  await userEvent.click(
    await screen.findByRole("button", { name: "手动启动研究" }),
  );
  expect(vi.mocked(api.startResearchRefresh).mock.calls[1][0]).not.toBe(
    firstKey,
  );
});

it("lets an explicit forbidden submission clear only the saved intent before starting a new request", async () => {
  vi.mocked(api.startResearchRefresh)
    .mockRejectedValueOnce(new Error("network timeout"))
    .mockRejectedValueOnce(new HttpError("mode disabled", 403, "FORBIDDEN"));
  const first = renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  await userEvent.click(
    await screen.findByRole("button", { name: "手动启动研究" }),
  );
  await screen.findByRole("button", { name: "重试本次提交" });
  const originalKey = vi.mocked(api.startResearchRefresh).mock.calls[0][0];
  first.unmount();

  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  await userEvent.click(
    await screen.findByRole("button", { name: "重试本次提交" }),
  );
  const clear = await screen.findByRole("button", {
    name: "清除未确认提交记录",
  });
  expect(vi.mocked(api.startResearchRefresh).mock.calls[1][0]).toBe(
    originalKey,
  );
  expect(
    screen.getByText(
      "只清除此页记录，不会取消后台任务。下一次启动将使用新请求。",
    ),
  ).toBeVisible();
  await userEvent.click(clear);
  expect(api.startResearchRefresh).toHaveBeenCalledTimes(2);
  expect(
    sessionStorage.getItem("wealthsail:manual-research:command:owner-a"),
  ).toBeNull();
  expect(
    screen.queryByRole("button", { name: "清除未确认提交记录" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(/提交结果待确认/)).not.toBeInTheDocument();
  expect(screen.getByText(/产生新的用量；普通刷新不会重跑/)).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "手动启动研究" }));
  expect(vi.mocked(api.startResearchRefresh).mock.calls[2][0]).not.toBe(
    originalKey,
  );
  expect(await screen.findByText(/后台处理中/)).toBeVisible();
});

it.each([
  ["network timeout", new Error("network timeout")],
  ["other HTTP status", new HttpError("unavailable", 503, "FORBIDDEN")],
  ["other error code", new HttpError("other code", 403, "OTHER")],
  ["untyped error", { status: 403, code: "FORBIDDEN" }],
])(
  "does not discard an unacknowledged intent for %s",
  async (_label, failure) => {
    vi.mocked(api.startResearchRefresh).mockRejectedValue(failure);
    renderWithSession(<DailyResearchRefresh actor="owner-a" />);
    await userEvent.click(
      await screen.findByRole("button", { name: "手动启动研究" }),
    );
    const retry = await screen.findByRole("button", { name: "重试本次提交" });
    expect(
      screen.queryByRole("button", { name: "清除未确认提交记录" }),
    ).not.toBeInTheDocument();
    await userEvent.click(retry);
    expect(vi.mocked(api.startResearchRefresh).mock.calls[1][0]).toBe(
      vi.mocked(api.startResearchRefresh).mock.calls[0][0],
    );
  },
);

it.each(["QUEUED", "RUNNING", "UNCERTAIN"] as const)(
  "does not permit a second model while an unfinished %s request is pending, including after 35 minutes",
  async (status) => {
    const pending = {
      ...request(status),
      startedAt: status === "QUEUED" ? null : "2020-01-01T00:00:00Z",
    };
    vi.mocked(api.getResearchRefresh).mockResolvedValue({
      enabled: true,
      request: pending,
    });
    vi.mocked(api.getResearchRefreshStatus).mockResolvedValue(pending);
    renderWithSession(<DailyResearchRefresh actor="owner-a" />);
    const button = await screen.findByRole("button", { name: "手动启动研究" });
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(api.startResearchRefresh).not.toHaveBeenCalled();
    expect(
      screen.queryByText(/可显式重新运行|可重新运行/),
    ).not.toBeInTheDocument();
  },
);

it("allows an explicit new submission for a completed uncertain request without promising immediate model execution", async () => {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: { ...request("UNCERTAIN"), completedAt: new Date().toISOString() },
  });
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  expect(
    await screen.findByRole("button", { name: "手动启动研究" }),
  ).toBeEnabled();
  expect(screen.getByText(/新请求.*等待/)).toBeVisible();
  expect(api.getResearchRefreshStatus).not.toHaveBeenCalled();
});

it("reloads the new report exactly once after a successful explicit manual request", async () => {
  const load = vi
    .fn()
    .mockResolvedValueOnce(report())
    .mockResolvedValue(report(true));
  vi.mocked(api.getResearchRefreshStatus).mockResolvedValue({
    ...request("READY"),
    reportRunId: currentRunId,
  });
  const onReady = vi.fn();
  renderWithSession(<ResearchPage load={load} onReady={onReady} />);
  await screen.findByText("保留的自动研究摘要");
  await userEvent.click(
    await screen.findByRole("button", { name: "手动启动研究" }),
  );
  expect(await screen.findByText("新手动研究摘要")).toBeInTheDocument();
  expect(
    screen.getByText("当前报告：手动研究版（显式重新采集与分析）"),
  ).toBeVisible();
  expect(screen.queryByText("保留的自动研究摘要")).not.toBeInTheDocument();
  expect(load).toHaveBeenCalledTimes(2);
  expect(onReady).toHaveBeenCalledTimes(1);
  expect(api.startResearchRefresh).toHaveBeenCalledTimes(1);
});

it.each(["FAILED", "UNCERTAIN"] as const)(
  "retains the previous report after %s and uses only this queue request's failure diagnostics",
  async (status) => {
    const load = vi.fn().mockResolvedValue(report());
    vi.mocked(api.getResearchRefreshStatus).mockResolvedValue({
      ...request(status),
      reportRunId: currentRunId,
      completedAt: new Date().toISOString(),
      errorClass: "ResearchValidationError",
      failureStage: "REPORT_VALIDATION",
      failureReasonCode: "DIRECTION_VALIDATION_FAILED",
      failureRuleId: "DIRECTION_EVIDENCE_INSUFFICIENT",
      failureItemIndex: 2,
    });
    renderWithSession(<ResearchPage load={load} />);
    await screen.findByText("保留的自动研究摘要");
    await userEvent.click(
      await screen.findByRole("button", { name: "手动启动研究" }),
    );
    await userEvent.click(await screen.findByText("查看执行诊断"));
    const diagnostics = within(
      await screen.findByRole("group", { name: "研究失败诊断" }),
    );
    expect(diagnostics.getByText("失败原因：价值方向的证据不足")).toBeVisible();
    expect(diagnostics.getByText(`研究执行ID：${currentRunId}`)).toBeVisible();
    expect(
      diagnostics.queryByText(new RegExp(previousRunId)),
    ).not.toBeInTheDocument();
    expect(screen.getByText("保留的自动研究摘要")).toBeInTheDocument();
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
    expect(screen.queryByText(/当前报告：手动研究版/)).not.toBeInTheDocument();
  },
);

it("shows safe industry diagnostics from the manual request without borrowing the retained report's codes", async () => {
  const retained = report();
  Object.assign(retained.report!, { failureIndustryCodes: ["US_TECH"] });
  const load = vi.fn().mockResolvedValue(retained);
  vi.mocked(api.getResearchRefreshStatus).mockResolvedValue(
    Object.assign(
      {
        ...request("FAILED"),
        reportRunId: currentRunId,
        completedAt: new Date().toISOString(),
        failureStage: "REPORT_VALIDATION" as const,
        failureRuleId: "DIRECTION_PLAN_SECTOR_UNREVIEWED" as const,
      },
      {
        failureIndustryCodes: [
          "ROBOT",
          "AI",
          "ROBOT",
          "<script>private-sector</script>",
        ],
      },
    ),
  );
  renderWithSession(<ResearchPage load={load} />);
  await screen.findByText("保留的自动研究摘要");
  await userEvent.click(
    await screen.findByRole("button", { name: "手动启动研究" }),
  );
  await userEvent.click(await screen.findByText("查看执行诊断"));
  const diagnosis = within(
    await screen.findByRole("group", { name: "研究失败诊断" }),
  );
  expect(diagnosis.getByText("相关行业代码：AI、ROBOT")).toBeVisible();
  expect(diagnosis.getByText(`研究执行ID：${currentRunId}`)).toBeVisible();
  expect(
    diagnosis.queryByText(/US_TECH|private-sector/),
  ).not.toBeInTheDocument();
  expect(screen.getByText("保留的自动研究摘要")).toBeInTheDocument();
});

it.each([
  ["READY", "✅ 今日投研已完成"],
  ["FAILED", "正式报告未通过"],
  ["UNCERTAIN", "执行异常 · 待核实"],
] as const)(
  "keeps MANUAL %s independent from a disabled automatic schedule",
  (state, label) => {
    const data = Object.assign(report(true), {
      state,
      scheduleState: "DISABLED" as const,
    });
    renderWithSession(<DailyResearchStatus data={data} />);
    expect(screen.getByText(label)).toBeVisible();
    expect(screen.queryByText("自动调度已关闭")).not.toBeInTheDocument();
  },
);

it("shows disabled automatic scheduling separately without relabeling the old report after a failed manual run", async () => {
  const data = Object.assign(report(), {
    state: "FAILED" as const,
    runMode: "MANUAL" as const,
    scheduleState: "DISABLED" as const,
    nextRunAt: "2026-11-03T23:00:00Z",
  });
  renderWithSession(<DailyResearchReport data={data} />);
  await userEvent.click(screen.getByText("执行与报告状态"));
  expect(screen.getByText(/自动调度已关闭/)).toBeVisible();
  expect(screen.getByText("正式报告未通过")).toBeVisible();
  expect(
    screen.queryByText(/当前报告：手动研究版|下次计划：/),
  ).not.toBeInTheDocument();
});

it("keeps the legacy DEBUG report label when the latest attempt is manual", () => {
  const data = Object.assign(report(), {
    runMode: "MANUAL" as const,
    reportRunMode: "DEBUG" as const,
  });
  renderWithSession(<DailyResearchReport data={data} />);
  expect(
    screen.getByText("当前报告：调试版（显式重新采集与分析）"),
  ).toBeVisible();
  expect(screen.queryByText(/当前报告：手动研究版/)).not.toBeInTheDocument();
});

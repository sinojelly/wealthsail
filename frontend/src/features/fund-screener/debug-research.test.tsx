import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DailyResearchRefresh from "./components/DailyResearchRefresh";
import { renderWithSession } from "../../test/render-with-session";
import * as api from "../../api/daily-research-debug";

vi.mock("../../api/daily-research-debug", () => ({
  getResearchRefresh: vi.fn(),
  startResearchRefresh: vi.fn(),
  getResearchRefreshStatus: vi.fn(),
}));
const request = (status = "QUEUED") => ({
  id: "debug-1",
  status,
  requestedAt: new Date().toISOString(),
  startedAt: null,
  completedAt: null,
  batchId: null,
  reportRunId: null,
  errorClass: null,
});
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: null,
  });
  vi.mocked(api.startResearchRefresh).mockResolvedValue(request() as never);
  vi.mocked(api.getResearchRefreshStatus).mockResolvedValue(
    request("RUNNING") as never,
  );
});
afterEach(() => vi.useRealTimers());
it("only explicitly starts collection and one model analysis, disabling an active request", async () => {
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  const button = await screen.findByRole("button", { name: "手动启动研究" });
  expect(
    screen.getByText(
      /将重新采集最新公共信息并调用一次 Codex，产生新的用量；普通刷新不会重跑/,
    ),
  ).toBeVisible();
  expect(api.startResearchRefresh).not.toHaveBeenCalled();
  await userEvent.click(button);
  expect(button).toBeDisabled();
  expect(await screen.findByText(/后台处理中 · 阶段待确认/)).toBeVisible();
  expect(api.startResearchRefresh).toHaveBeenCalledWith(
    expect.stringMatching(/^[0-9a-f-]{36}$/),
  );
});
it("retries an uncertain submission using the same command UUID", async () => {
  vi.mocked(api.startResearchRefresh).mockRejectedValueOnce(
    new Error("网络中断"),
  );
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  await userEvent.click(
    await screen.findByRole("button", { name: "手动启动研究" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "重试本次提交" }),
  );
  expect(vi.mocked(api.startResearchRefresh).mock.calls[1][0]).toBe(
    vi.mocked(api.startResearchRefresh).mock.calls[0][0],
  );
});
it("recovers the current user's active request without starting another model", async () => {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: request("RUNNING") as never,
  });
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  expect(
    await screen.findByRole("button", { name: "手动启动研究" }),
  ).toBeDisabled();
  expect(await screen.findByText(/后台处理中 · 阶段待确认/)).toBeVisible();
  expect(api.startResearchRefresh).not.toHaveBeenCalled();
});
it("shows failed analysis with its safe reason and keeps the previous report", async () => {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: { ...request("FAILED"), errorClass: "SourceUnavailable" } as never,
  });
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  expect(
    await screen.findByText(
      /采集或分析失败.*SourceUnavailable.*保留上一份有效报告/,
    ),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "手动启动研究" })).toBeEnabled();
});
it("hides the control when debug capability is unavailable", async () => {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: false,
    request: null,
  });
  renderWithSession(<DailyResearchRefresh actor="member" />);
  await waitFor(() => expect(api.getResearchRefresh).toHaveBeenCalled());
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
it("stops polling and requests current results after READY", async () => {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: request("RUNNING") as never,
  });
  vi.mocked(api.getResearchRefreshStatus).mockResolvedValue(
    request("READY") as never,
  );
  const onReady = vi.fn();
  renderWithSession(<DailyResearchRefresh actor="owner-a" onReady={onReady} />);
  expect(await screen.findByText(/手动研究已完成/)).toBeVisible();
  expect(onReady).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "手动启动研究" })).toBeEnabled();
});
it("rechecks an old running request and recovers the server's completed report", async () => {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: {
      ...request("RUNNING"),
      requestedAt: "2020-01-01T00:00:00Z",
      startedAt: "2020-01-01T00:00:01Z",
    } as never,
  });
  vi.mocked(api.getResearchRefreshStatus).mockResolvedValue(
    request("READY") as never,
  );
  const onReady = vi.fn();
  renderWithSession(<DailyResearchRefresh actor="owner-a" onReady={onReady} />);
  expect(await screen.findByText(/手动研究已完成/)).toBeVisible();
  expect(api.getResearchRefreshStatus).toHaveBeenCalledWith(
    "debug-1",
    expect.anything(),
  );
  expect(api.startResearchRefresh).not.toHaveBeenCalled();
  expect(onReady).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(/超过35分钟/)).not.toBeInTheDocument();
});

it("checks an uncertain server request again without restarting the model", async () => {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: {
      ...request("UNCERTAIN"),
      errorClass: "ExecutionWindowExpired",
    } as never,
  });
  vi.mocked(api.getResearchRefreshStatus).mockResolvedValue(
    request("READY") as never,
  );
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  expect(await screen.findByText(/手动研究已完成/)).toBeVisible();
  expect(api.startResearchRefresh).not.toHaveBeenCalled();
});

it("does not keep polling a terminal uncertain execution", async () => {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: {
      ...request("UNCERTAIN"),
      completedAt: new Date().toISOString(),
      errorClass: "TimeoutError",
    } as never,
  });
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  expect(await screen.findByText(/执行结果待确认.*TimeoutError/)).toBeVisible();
  expect(api.getResearchRefreshStatus).not.toHaveBeenCalled();
});

it("keeps the execution outcome visible while technical diagnostics are collapsed", async () => {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: {
      ...request("UNCERTAIN"),
      completedAt: new Date().toISOString(),
      errorClass: "TimeoutError",
      failureStage: "MODEL_EXECUTION",
      failureReasonCode: "TIMEOUT",
    } as never,
  });
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  expect(await screen.findByText(/执行结果待确认.*TimeoutError/)).toBeVisible();
  const details = screen.getByText("查看执行诊断").closest("details");
  expect(details).not.toHaveAttribute("open");
  expect(screen.getByText("原因代码：TIMEOUT")).not.toBeVisible();
  await userEvent.click(screen.getByText("查看执行诊断"));
  expect(details).toHaveAttribute("open");
  expect(screen.getByText("原因代码：TIMEOUT")).toBeVisible();
  expect(api.startResearchRefresh).not.toHaveBeenCalled();
});

it("counts execution from the worker start rather than time spent queued", async () => {
  const running = {
    ...request("RUNNING"),
    requestedAt: "2020-01-01T00:00:00Z",
    startedAt: new Date().toISOString(),
  };
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: running as never,
  });
  vi.mocked(api.getResearchRefreshStatus).mockResolvedValue(running as never);
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  expect(await screen.findByText(/后台处理中 · 阶段待确认/)).toBeVisible();
  expect(screen.getByRole("button", { name: "手动启动研究" })).toBeDisabled();
  expect(screen.queryByText(/超过35分钟/)).not.toBeInTheDocument();
});

it("keeps a completed report terminal beyond its original waiting deadline", async () => {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: {
      ...request("READY"),
      requestedAt: "2020-01-01T00:00:00Z",
    } as never,
  });
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  expect(await screen.findByText(/手动研究已完成/)).toBeVisible();
  expect(screen.queryByText(/超过35分钟/)).not.toBeInTheDocument();
});

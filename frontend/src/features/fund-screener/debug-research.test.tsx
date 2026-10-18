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
  const button = await screen.findByRole("button", { name: "重新采集并分析" });
  expect(screen.getByText(/重新抓取新闻并运行一次模型/)).toBeVisible();
  expect(api.startResearchRefresh).not.toHaveBeenCalled();
  await userEvent.click(button);
  expect(button).toBeDisabled();
  expect(await screen.findByText(/正在采集与分析/)).toBeVisible();
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
    await screen.findByRole("button", { name: "重新采集并分析" }),
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
    await screen.findByRole("button", { name: "重新采集并分析" }),
  ).toBeDisabled();
  expect(await screen.findByText(/正在采集与分析/)).toBeVisible();
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
  expect(screen.getByRole("button", { name: "重新采集并分析" })).toBeEnabled();
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
  expect(await screen.findByText(/调试研究已完成/)).toBeVisible();
  expect(onReady).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "重新采集并分析" })).toBeEnabled();
});
it("does not poll a request beyond the 35 minute uncertainty limit", async () => {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: {
      ...request("RUNNING"),
      requestedAt: "2020-01-01T00:00:00Z",
    } as never,
  });
  renderWithSession(<DailyResearchRefresh actor="owner-a" />);
  expect(await screen.findByText(/超过35分钟.*待确认/)).toBeVisible();
  expect(api.getResearchRefreshStatus).not.toHaveBeenCalled();
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
  expect(await screen.findByText(/调试研究已完成/)).toBeVisible();
  expect(screen.queryByText(/超过35分钟/)).not.toBeInTheDocument();
});

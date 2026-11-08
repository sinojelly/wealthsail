import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as api from "../../api/daily-research-debug";
import { HttpError } from "../../api/client";
import { renderWithSession } from "../../test/render-with-session";
import DailyResearchRefresh from "./components/DailyResearchRefresh";

vi.mock("../../api/daily-research-debug", () => ({
  getResearchRefresh: vi.fn(),
  startResearchRefresh: vi.fn(),
  getResearchRefreshStatus: vi.fn(),
  cancelResearchRefresh: vi.fn(),
}));
const queued: api.DebugResearchRequest = {
  id: "queued-one",
  status: "QUEUED",
  requestMode: "MANUAL",
  requestedAt: "2026-11-08T12:00:00+08:00",
  startedAt: null,
  completedAt: null,
  batchId: null,
  reportRunId: null,
  errorClass: null,
  queueExpiresAt: "2026-11-08T12:05:00+08:00",
  canCancel: true,
  blockedReasonCode: "MODEL_GUARD_ACTIVE",
  queuePosition: 1,
};
function setup(value: api.DebugResearchRequest) {
  vi.mocked(api.getResearchRefresh).mockResolvedValue({
    enabled: true,
    request: value,
  });
  vi.mocked(api.getResearchRefreshStatus).mockResolvedValue(value);
  return renderWithSession(<DailyResearchRefresh actor="queue-owner" />);
}
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
});

it("does not revive a cancelled queue when an older in-flight poll arrives late", async () => {
  let finishPoll!: (value: api.DebugResearchRequest) => void;
  const oldPoll = new Promise<api.DebugResearchRequest>((resolve) => {finishPoll=resolve;});
  vi.mocked(api.getResearchRefresh).mockResolvedValue({enabled:true,request:queued});
  vi.mocked(api.getResearchRefreshStatus).mockReturnValue(oldPoll);
  vi.mocked(api.cancelResearchRefresh).mockResolvedValue({...queued,status:"CANCELLED",completedAt:queued.queueExpiresAt!,canCancel:false});
  renderWithSession(<DailyResearchRefresh actor="late-cancel-owner" />);
  await userEvent.click(await screen.findByRole("button",{name:"取消排队研究"}));
  await screen.findByText(/排队研究已取消/);
  await act(async () => {finishPoll(queued); await oldPoll;});
  expect(screen.getByText(/排队研究已取消/)).toBeVisible();
  expect(screen.queryByRole("button",{name:"取消排队研究"})).not.toBeInTheDocument();
});

it("distinguishes lost exit evidence and maintenance invalidation from the five-minute deadline", async () => {
  const view = setup({
    ...queued,
    modelExitEvidenceState: "LOCAL_EVIDENCE_NOT_RETAINED",
    modelExitEvidenceCheckedAt: queued.requestedAt,
  });
  expect(
    await screen.findByText(/旧研究退出证据未保留，需要本机受控维护/),
  ).toBeVisible();
  view.unmount();
  setup({
    ...queued,
    id: "expired-maintenance",
    status: "EXPIRED",
    completedAt: queued.queueExpiresAt!,
    errorClass: "QueueInvalidatedByMaintenance",
    canCancel: false,
  });
  expect(await screen.findByText(/旧排队请求已在本机维护中终止/)).toBeVisible();
  expect(screen.queryByText(/排队等待超过5分钟/)).not.toBeInTheDocument();
  expect(api.startResearchRefresh).not.toHaveBeenCalled();
});

it("explains a blocked model slot and cancels the exact queued request without dispatching AI", async () => {
  vi.mocked(api.cancelResearchRefresh).mockResolvedValue({
    ...queued,
    status: "CANCELLED",
    completedAt: queued.queueExpiresAt!,
    canCancel: false,
  });
  setup(queued);
  expect(await screen.findByText(/排队等待模型槽/)).toBeVisible();
  expect(screen.getByText(/上次研究.*退出确认/)).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "取消排队研究" }));
  expect(await screen.findByText(/排队研究已取消/)).toBeVisible();
  expect(api.cancelResearchRefresh).toHaveBeenCalledExactlyOnceWith(
    "queued-one",
  );
  expect(api.startResearchRefresh).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "手动启动研究" })).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "取消排队研究" }),
  ).not.toBeInTheDocument();
});

it("expired queues never restart automatically after the old model slot recovers", async () => {
  setup({
    ...queued,
    status: "EXPIRED",
    completedAt: queued.queueExpiresAt!,
    errorClass: "QueueWaitExpired",
    canCancel: false,
  });
  expect(await screen.findByText(/排队等待超过5分钟.*已终止/)).toBeVisible();
  expect(screen.getByText(/不会自动重试.*重新点击/)).toBeVisible();
  expect(screen.getByRole("button", { name: "手动启动研究" })).toBeEnabled();
  expect(api.startResearchRefresh).not.toHaveBeenCalled();
});

it.each([
  ["COLLECTING", "正在采集公共数据"],
  ["WAITING_MODEL_SLOT", "等待模型槽，尚未调用 AI"],
  ["MODEL_RUNNING", "AI 分析中"],
  ["VERIFYING_OUTPUT", "正在核对引用来源"],
  ["REPORT_VALIDATION", "报告验证中"],
  ["PERSISTING", "正在保存报告"],
] as const)(
  "shows truthful %s phase without exposing cancel for started work",
  async (executionStage, label) => {
    setup({
      ...queued,
      status: "RUNNING",
      startedAt: new Date().toISOString(),
      canCancel: false,
      executionStage,
    });
    expect(await screen.findByText(new RegExp(label))).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "取消排队研究" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "手动启动研究" })).toBeDisabled();
  },
);

it("refreshes state after losing the cancellation race and never retries a model command", async () => {
  vi.mocked(api.cancelResearchRefresh).mockRejectedValue(
    new HttpError("private provider details", 409, "VERSION_CONFLICT"),
  );
  setup(queued);
  const cancel = await screen.findByRole("button", { name: "取消排队研究" });
  vi.mocked(api.getResearchRefreshStatus).mockResolvedValue({
    ...queued,
    status: "RUNNING",
    startedAt: new Date().toISOString(),
    executionStage: "COLLECTING",
    canCancel: false,
  });
  await userEvent.click(cancel);
  expect(await screen.findByText(/正在采集公共数据/)).toBeVisible();
  expect(
    screen.queryByText(/private provider details/),
  ).not.toBeInTheDocument();
  expect(api.startResearchRefresh).not.toHaveBeenCalled();
});

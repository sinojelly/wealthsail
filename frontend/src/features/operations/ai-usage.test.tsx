import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OperationsPage from "./OperationsPage";
import { renderWithSession } from "../../test/render-with-session";
import { aiUsageFixture } from "../../test/ai-usage-fixture";
import {
  auditFixture,
  opsFreshnessFixture,
  opsFundEstimateFixture,
  opsJobsFixture,
  opsOutboxFixture,
} from "../../test/fixtures";

const runFundEstimate = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));

vi.mock("../../api/hooks", () => ({
  useOpsJobs: () => ({ data: opsJobsFixture, error: null }),
  useOpsFreshness: () => ({ data: opsFreshnessFixture, error: null }),
  useOpsOutbox: () => ({ data: opsOutboxFixture, error: null }),
  useOpsAudit: () => ({ data: auditFixture, error: null }),
  useOpsFundEstimate: () => ({ data: opsFundEstimateFixture, error: null }),
  useRunFundEstimate: () => ({
    mutateAsync: runFundEstimate,
    isPending: false,
    error: null,
  }),
}));

function response(data: unknown, status = 200) {
  return new Response(
    JSON.stringify({ data, meta: { requestId: "test-ai-usage" } }),
    { status },
  );
}

afterEach(() => vi.unstubAllGlobals());

it("keeps the fund estimate and shared health overview while switching every operations tab", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(aiUsageFixture)));
  renderWithSession(<OperationsPage />);

  for (const name of ["AI 用量", "审计日志", "作业运行"]) {
    await user.click(screen.getByRole("tab", { name }));
    expect(screen.getByRole("tab", { name })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      screen.getByRole("heading", { name: "场外基金盘中估值" }),
    ).toBeVisible();
    expect(screen.getByText("8 / 12 只已生成")).toBeVisible();
    expect(screen.getByText("作业成功率")).toBeVisible();
    expect(screen.getByText("100.0%")).toBeVisible();
    expect(screen.getByText("最近作业耗时")).toBeVisible();
    expect(screen.getByText("42.8s", { selector: "strong" })).toBeVisible();
    expect(screen.getByText("净值快照新鲜度")).toBeVisible();
    expect(screen.getByText("2026/08/23 16:00:08")).toBeVisible();
    expect(screen.getByText("消息待投递")).toBeVisible();
    expect(screen.getByText(/UNKNOWN\s+2/)).toBeVisible();
    expect(screen.getByText("运维视图为脱敏数据")).toBeVisible();
    if (name === "AI 用量") {
      expect(await screen.findByText("a***@example.com")).toBeVisible();
      await user.click(
        screen.getByRole("button", { name: "立即计算基金估值" }),
      );
      expect(runFundEstimate).toHaveBeenCalledTimes(1);
      expect(
        screen.getByRole("button", { name: "刷新 AI 用量" }),
      ).toBeEnabled();
    } else if (name === "审计日志") {
      expect(screen.getByText("policy.confirm")).toBeVisible();
    } else {
      expect(screen.getByText("report.postmarket.generate")).toBeVisible();
    }
  }
});

it("loads AI usage only when selected and expands each inference while keeping business outcome separate", async () => {
  const user = userEvent.setup();
  const fetcher = vi.fn().mockResolvedValue(response(aiUsageFixture));
  vi.stubGlobal("fetch", fetcher);
  renderWithSession(<OperationsPage />);
  expect(fetcher).not.toHaveBeenCalled();

  await user.click(screen.getByRole("tab", { name: "AI 用量" }));
  expect(await screen.findByText("a***@example.com")).toBeInTheDocument();
  expect(screen.getByText("pro")).toBeInTheDocument();
  expect(screen.getByText("88%")).toBeInTheDocument();
  expect(screen.getByText("10.50 credits")).toBeInTheDocument();
  const row = screen.getByRole("row", { name: /基金机会日报/ });
  expect(within(row).getByText("部分成功")).toBeInTheDocument();
  expect(within(row).getByText("成功")).toBeInTheDocument();
  expect(within(row).getByText("1,300")).toBeInTheDocument();
  expect(within(row).getAllByText(/快照不完整，无法估算/)).toHaveLength(2);
  expect(within(row).queryByText(/2 百分点/)).not.toBeInTheDocument();
  expect(within(row).getByText("2026/10/27 15:30:00")).toBeInTheDocument();
  expect(screen.queryByText("TimeoutError")).not.toBeInTheDocument();
  await user.click(within(row).getByRole("button", { name: /展开.*2 次推理/ }));
  expect(screen.getByText("TimeoutError")).toBeInTheDocument();
  expect(screen.getByText("2 百分点（估算）")).toBeInTheDocument();
  expect(screen.getByText(/缓存输入已包含在输入/)).toBeInTheDocument();
});

it.each([
  ["DAILY_RESEARCH", "research", "日常投研", "研究推理"],
  ["REPORT", "completion", "研报生成", "文本生成"],
  ["CHAT", "chat", "智能问答", "对话推理"],
  ["UNKNOWN_TASK", "unknown_call", "未知任务类型", "未知调用类型"],
])(
  "renders %s tasks and %s calls in Chinese while preserving source codes in titles",
  async (taskType, callType, taskLabel, callLabel) => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({
          ...aiUsageFixture,
          tasks: [
            {
              ...aiUsageFixture.tasks[0],
              taskType,
              calls: aiUsageFixture.tasks[0].calls.map((call) => ({
                ...call,
                callType,
              })),
            },
          ],
        }),
      ),
    );
    renderWithSession(<OperationsPage />);
    await user.click(screen.getByRole("tab", { name: "AI 用量" }));
    await screen.findByText("基金机会日报");
    const row = screen.getByRole("row", {
      name: /基金机会日报/,
    });
    expect(within(row).getByText(taskLabel)).toHaveAttribute("title", taskType);
    expect(within(row).queryByText(taskType)).not.toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: /展开/ }));
    const details = screen.getByRole("region", {
      name: "基金机会日报推理详情",
    });
    expect(within(details).getAllByText(callLabel)).toHaveLength(2);
    expect(within(details).getAllByText(callLabel)[0]).toHaveAttribute(
      "title",
      callType,
    );
    expect(within(details).queryByText(callType)).not.toBeInTheDocument();
  },
);

it("paginates by task and refreshes the selected page", async () => {
  const user = userEvent.setup();
  const requested: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      requested.push(url);
      return response(
        url.includes("offset=20")
          ? {
              ...aiUsageFixture,
              total: 21,
              offset: 20,
              tasks: [
                {
                  ...aiUsageFixture.tasks[0],
                  id: "task-21",
                  taskName: "下一页研究",
                },
              ],
            }
          : { ...aiUsageFixture, total: 21 },
      );
    }),
  );
  renderWithSession(<OperationsPage />);
  await user.click(screen.getByRole("tab", { name: "AI 用量" }));
  await screen.findByText("基金机会日报");
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "下一页" }));
  expect(await screen.findByText("下一页研究")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "刷新 AI 用量" }));
  await waitFor(() =>
    expect(
      requested.filter((url) => url.endsWith("limit=20&offset=20")),
    ).toHaveLength(2),
  );
});

it("shows missing tokens and reset quota as unknown instead of zero and explains account restrictions", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      response({
        ...aiUsageFixture,
        account: {
          ...aiUsageFixture.account,
          status: "RESTRICTED",
          email: null,
          planType: null,
          authType: null,
          windows: [],
          credits: null,
        },
        tasks: [
          {
            ...aiUsageFixture.tasks[0],
            inferenceStatus: "NONE",
            businessStatus: "DEGRADED",
            inputTokens: null,
            cachedTokens: null,
            outputTokens: null,
            totalTokens: null,
            quota: {
              windows: [
                {
                  key: "primary",
                  label: "5 小时额度",
                  consumedPercent: null,
                  status: "PERIOD_CHANGED",
                },
              ],
              credits: { consumed: null, status: "RESET_OR_TOPUP" },
            },
          },
        ],
      }),
    ),
  );
  renderWithSession(<OperationsPage />);
  await user.click(screen.getByRole("tab", { name: "AI 用量" }));
  expect(
    await screen.findByText(/仅平台管理员可查看共享 AI 账户/),
  ).toBeInTheDocument();
  const row = screen.getByRole("row", { name: /基金机会日报/ });
  expect(within(row).getAllByText("未知")).toHaveLength(4);
  expect(within(row).getByText("未调用")).toBeInTheDocument();
  expect(within(row).getByText("降级完成")).toBeInTheDocument();
  expect(within(row).getByText(/周期变化，无法估算/)).toBeInTheDocument();
  expect(within(row).getByText(/重置或充值，无法估算/)).toBeInTheDocument();
  expect(screen.queryByText("0 credits")).not.toBeInTheDocument();
});

it("offers retry after request failure and renders an empty history without inventing old usage", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ error: { message: "额度服务暂不可用" } }),
          { status: 503 },
        ),
      )
      .mockResolvedValueOnce(
        response({ ...aiUsageFixture, tasks: [], total: 0 }),
      ),
  );
  renderWithSession(<OperationsPage />);
  await user.click(screen.getByRole("tab", { name: "AI 用量" }));
  expect(
    await screen.findByRole("alert", { name: "AI 用量加载失败" }),
  ).toHaveTextContent("额度服务暂不可用");
  expect(
    screen.getByRole("heading", { name: "场外基金盘中估值" }),
  ).toBeVisible();
  expect(screen.getByText("作业成功率")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "重试 AI 用量" }));
  expect(await screen.findByText(/尚无 AI 用量记录/)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "立即计算基金估值" }),
  ).toBeEnabled();
  expect(screen.getByText(/旧任务没有采集数据时不回填/)).toBeInTheDocument();
});

it("shows an explicit loading state before account data arrives", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => new Promise<Response>(() => {})),
  );
  renderWithSession(<OperationsPage />);
  await userEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
  expect(
    screen.getByRole("status", { name: "AI 用量加载中" }),
  ).toHaveTextContent("正在读取 AI 用量");
  expect(
    screen.getByRole("heading", { name: "场外基金盘中估值" }),
  ).toBeVisible();
  expect(screen.getByText("作业成功率")).toBeVisible();
});

it("never displays negative or out-of-range balance values as valid consumption", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      response({
        ...aiUsageFixture,
        account: {
          ...aiUsageFixture.account,
          windows: [
            { ...aiUsageFixture.account.windows[0], remainingPercent: 120 },
          ],
          credits: { balance: "-3", hasCredits: true, unlimited: false },
        },
        tasks: [
          {
            ...aiUsageFixture.tasks[0],
            inputTokens: -10,
            cachedTokens: null,
            outputTokens: null,
            totalTokens: null,
            quota: {
              windows: [
                {
                  key: "primary",
                  label: "5 小时额度",
                  consumedPercent: -2,
                  status: "ESTIMATED",
                },
              ],
              credits: { consumed: "-5", status: "ESTIMATED" },
            },
          },
        ],
      }),
    ),
  );
  renderWithSession(<OperationsPage />);
  await userEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
  await screen.findByText("基金机会日报");
  expect(
    screen.queryByText(/120%|-2 百分点|-3 credits|-5 credits|-10/),
  ).not.toBeInTheDocument();
  const row = screen.getByRole("row", { name: /基金机会日报/ });
  expect(within(row).getAllByText("未知").length).toBeGreaterThanOrEqual(4);
});

it("translates server limitation codes into actionable operator messages", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      response({
        ...aiUsageFixture,
        account: {
          ...aiUsageFixture.account,
          limitations: ["QUOTA_UNAVAILABLE", "CREDITS_UNAVAILABLE"],
        },
        tasks: [],
        total: 0,
        limitations: [
          "ACCOUNT_DELTA_ESTIMATE",
          "HISTORY_FROM_INSTALLATION",
          "USAGE_LEDGER_NOT_MIGRATED",
        ],
      }),
    ),
  );
  renderWithSession(<OperationsPage />);
  await userEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
  expect(
    await screen.findByText("供应商暂未提供可用的额度窗口信息。"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("供应商暂未提供 credits 余额，不视为零余额。"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("用量记录尚未初始化，请先完成数据库升级。"),
  ).toBeInTheDocument();
  expect(
    screen.queryByText("USAGE_LEDGER_NOT_MIGRATED"),
  ).not.toBeInTheDocument();
});

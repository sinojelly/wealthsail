import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OperationsPage from "./OperationsPage";
import { renderWithSession } from "../../test/render-with-session";
import { aiUsageFixture } from "../../test/ai-usage-fixture";
import { installLogin, logout } from "../../app/auth-session";
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

afterEach(async () => {
  await logout({ notifyServer: false });
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("keeps the fund estimate and shared health overview while switching every operations tab", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response(aiUsageFixture)),
  );
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
  const fetcher = vi.fn(async () => response(aiUsageFixture));
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
      vi.fn(async () =>
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
  expect(requested.filter((url) => url.includes("view=account"))).toHaveLength(
    1,
  );
  await user.click(screen.getByRole("button", { name: "刷新 AI 用量" }));
  await waitFor(() =>
    expect(
      requested.filter((url) => url.endsWith("limit=20&offset=20")),
    ).toHaveLength(2),
  );
  expect(
    requested.filter((url) =>
      url.includes("view=account&refresh_account=true"),
    ),
  ).toHaveLength(1);
});

it("refreshes tasks every 30 seconds and the account separately every 180 seconds", async () => {
  vi.useFakeTimers();
  const requested: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      requested.push(url);
      const { account, ...tasks } = aiUsageFixture;
      return response(url.includes("view=account") ? { account } : tasks);
    }),
  );
  renderWithSession(<OperationsPage />);
  expect(requested).toHaveLength(0);
  fireEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.getByText("基金机会日报")).toBeVisible();
  expect(screen.getByText("a***@example.com")).toBeVisible();
  expect(requested.filter((url) => url.includes("view=account"))).toHaveLength(
    1,
  );
  expect(requested.filter((url) => url.includes("view=tasks"))).toHaveLength(1);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
  expect(requested.filter((url) => url.includes("view=tasks"))).toHaveLength(2);
  expect(requested.filter((url) => url.includes("view=account"))).toHaveLength(
    1,
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(150_000);
  });
  expect(requested.filter((url) => url.includes("view=account"))).toHaveLength(
    2,
  );
  expect(requested.some((url) => url.includes("refresh_account=true"))).toBe(
    false,
  );

  fireEvent.click(screen.getByRole("tab", { name: "作业运行" }));
  const count = requested.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(180_000);
  });
  expect(requested).toHaveLength(count);
});

it("keeps task history available while the independent account query is loading", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("view=account")) return new Promise<Response>(() => {});
      return Promise.resolve(
        response({
          tasks: aiUsageFixture.tasks,
          total: 1,
          limit: 20,
          offset: 0,
          limitations: [],
        }),
      );
    }),
  );
  renderWithSession(<OperationsPage />);
  await userEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
  expect(await screen.findByText("基金机会日报")).toBeVisible();
  expect(screen.getByRole("status", { name: "AI 账户加载中" })).toBeVisible();
  expect(screen.getByText("作业成功率")).toBeVisible();
});

it("labels a retained server snapshot as historical with its last successful observation time", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const { account, ...tasks } = aiUsageFixture;
      return response(
        url.includes("view=account")
          ? {
              account: {
                ...account,
                status: "STALE",
                limitations: ["ACCOUNT_STALE"],
              },
            }
          : tasks,
      );
    }),
  );
  renderWithSession(<OperationsPage />);
  await userEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
  const account = await screen.findByRole("region", { name: "当前 AI 账户" });
  expect(within(account).getByText("a***@example.com")).toBeVisible();
  expect(within(account).getByText("历史快照")).toBeVisible();
  expect(
    within(account).getByText(/最后成功观测时间 2026\/10\/27 15:30:00/),
  ).toBeVisible();
  expect(within(account).getByText(/本次账户查询失败/)).toBeVisible();
  expect(within(account).queryByText("可用")).not.toBeInTheDocument();
});

it("retains the previous account as stale when a manual metadata refresh fails without hiding tasks", async () => {
  const requested: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      requested.push(url);
      if (url.includes("refresh_account=true")) {
        return response({ error: "not used" }, 503);
      }
      const { account, ...tasks } = aiUsageFixture;
      return response(url.includes("view=account") ? { account } : tasks);
    }),
  );
  renderWithSession(<OperationsPage />);
  await userEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
  await screen.findByText("a***@example.com");
  await userEvent.click(screen.getByRole("button", { name: "刷新 AI 用量" }));
  const account = screen.getByRole("region", { name: "当前 AI 账户" });
  expect(await within(account).findByText("历史快照")).toBeVisible();
  expect(within(account).getByText("a***@example.com")).toBeVisible();
  expect(
    within(account).getByText(/最后成功观测时间 2026\/10\/27 15:30:00/),
  ).toBeVisible();
  expect(screen.getByText("基金机会日报")).toBeVisible();
  expect(screen.getByRole("alert", { name: "AI 账户加载失败" })).toBeVisible();
  expect(
    requested.filter((url) => url.includes("refresh_account=true")),
  ).toHaveLength(1);
});

it("returns to recent account information after automatic refresh recovers from a manual failure", async () => {
  vi.useFakeTimers();
  let accountRequests = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("refresh_account=true")) return response({}, 503);
      if (url.includes("view=account")) {
        accountRequests += 1;
        return response({
          account: {
            ...aiUsageFixture.account,
            observedAt:
              accountRequests > 1
                ? "2026-10-27T07:33:00Z"
                : aiUsageFixture.account.observedAt,
          },
        });
      }
      return response(aiUsageFixture);
    }),
  );
  renderWithSession(<OperationsPage />);
  fireEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  fireEvent.click(screen.getByRole("button", { name: "刷新 AI 用量" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.getByText("历史快照")).toBeVisible();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(180_000);
  });
  expect(screen.queryByText("历史快照")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("alert", { name: "AI 账户加载失败" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/观测时间 2026\/10\/27 15:33:00/)).toBeVisible();
});

it.each(["manual", "automatic"])(
  "retains a known successful snapshot as stale after a 200 UNAVAILABLE response on %s refresh",
  async (mode) => {
    vi.useFakeTimers();
    let accountRequests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (!url.includes("view=account")) return response(aiUsageFixture);
        accountRequests += 1;
        return response({
          account:
            accountRequests === 1
              ? aiUsageFixture.account
              : {
                  ...aiUsageFixture.account,
                  status: "UNAVAILABLE",
                  email: null,
                  planType: null,
                  authType: null,
                  observedAt: null,
                  windows: [],
                  credits: null,
                  limitations: ["ACCOUNT_READ_FAILED"],
                },
        });
      }),
    );
    renderWithSession(<OperationsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    if (mode === "manual") {
      fireEvent.click(screen.getByRole("button", { name: "刷新 AI 用量" }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
    } else {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(180_000);
      });
    }
    const account = screen.getByRole("region", { name: "当前 AI 账户" });
    expect(within(account).getByText("历史快照")).toBeVisible();
    expect(within(account).getByText("a***@example.com")).toBeVisible();
    expect(within(account).getByText("88%")).toBeVisible();
    expect(
      within(account).getByText(/最后成功观测时间 2026\/10\/27 15:30:00/),
    ).toBeVisible();
    expect(screen.getByText("基金机会日报")).toBeVisible();
  },
);

it.each(["manual", "automatic"])(
  "clears the prior Codex identity when %s refresh reports changed runner authentication",
  async (mode) => {
    vi.useFakeTimers();
    let accountRequests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (!url.includes("view=account")) return response(aiUsageFixture);
        accountRequests += 1;
        return response({
          account:
            accountRequests === 1
              ? aiUsageFixture.account
              : {
                  status: "UNAVAILABLE",
                  provider: "codex",
                  email: null,
                  planType: null,
                  authType: null,
                  observedAt: null,
                  windows: [],
                  credits: null,
                  limitations: ["ACCOUNT_AUTH_CHANGED"],
                },
        });
      }),
    );
    renderWithSession(<OperationsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(screen.getByText("a***@example.com")).toBeVisible();
    if (mode === "manual") {
      fireEvent.click(screen.getByRole("button", { name: "刷新 AI 用量" }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
    } else {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(180_000);
      });
    }
    const account = screen.getByRole("region", { name: "当前 AI 账户" });
    expect(
      within(account).queryByText("a***@example.com"),
    ).not.toBeInTheDocument();
    expect(
      within(account).queryByText("10.50 credits"),
    ).not.toBeInTheDocument();
    expect(within(account).queryByText("88%")).not.toBeInTheDocument();
    expect(within(account).queryByText("历史快照")).not.toBeInTheDocument();
    expect(
      within(account).getByText(/认证已变更.*刷新.*新.*观测/),
    ).toBeVisible();
    expect(
      within(account).queryByText("供应商未提供完整的账户用量信息。"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("基金机会日报")).toBeVisible();
  },
);

it("clears the prior account when the local provider reports UNAVAILABLE with CODEX_NOT_CONFIGURED", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (!url.includes("refresh_account=true"))
        return response(aiUsageFixture);
      return response({
        account: {
          status: "UNAVAILABLE",
          provider: "codex",
          email: null,
          planType: null,
          authType: null,
          observedAt: null,
          windows: [],
          credits: null,
          limitations: ["CODEX_NOT_CONFIGURED"],
        },
      });
    }),
  );
  renderWithSession(<OperationsPage />);
  await userEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
  await screen.findByText("a***@example.com");
  await userEvent.click(screen.getByRole("button", { name: "刷新 AI 用量" }));
  const account = screen.getByRole("region", { name: "当前 AI 账户" });
  expect(
    await within(account).findByText("尚未配置 Codex 账户，请联系运维处理。"),
  ).toBeVisible();
  expect(
    within(account).queryByText("a***@example.com"),
  ).not.toBeInTheDocument();
  expect(within(account).queryByText("10.50 credits")).not.toBeInTheDocument();
  expect(within(account).queryByText("88%")).not.toBeInTheDocument();
  expect(within(account).queryByText("历史快照")).not.toBeInTheDocument();
  expect(screen.getByText("基金机会日报")).toBeVisible();
});

it("forgets private account fields after forbidden access even if the next refresh has a transport failure", async () => {
  let forcedRequests = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("refresh_account=true")) {
        forcedRequests += 1;
        return response({}, forcedRequests === 1 ? 403 : 503);
      }
      return response(aiUsageFixture);
    }),
  );
  renderWithSession(<OperationsPage />);
  await userEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
  await screen.findByText("a***@example.com");
  await userEvent.click(screen.getByRole("button", { name: "刷新 AI 用量" }));
  await waitFor(() => {
    expect(screen.queryByText("a***@example.com")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新 AI 用量" })).toBeEnabled();
  });
  await userEvent.click(screen.getByRole("button", { name: "刷新 AI 用量" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "重试账户信息" })).toBeEnabled(),
  );
  expect(screen.queryByText("a***@example.com")).not.toBeInTheDocument();
  expect(screen.queryByText("88%")).not.toBeInTheDocument();
  expect(screen.getByText("基金机会日报")).toBeVisible();
});

it.each(["UNAUTHENTICATED", "RESTRICTED", "NOT_CONFIGURED"])(
  "clears old account details when refreshed status becomes %s",
  async (status) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const { account, ...tasks } = aiUsageFixture;
        return response(
          url.includes("view=account")
            ? {
                account: url.includes("refresh_account=true")
                  ? {
                      ...account,
                      status,
                      email: null,
                      planType: null,
                      authType: null,
                      windows: [],
                      credits: null,
                    }
                  : account,
              }
            : tasks,
        );
      }),
    );
    renderWithSession(<OperationsPage />);
    await userEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
    await screen.findByText("a***@example.com");
    await userEvent.click(screen.getByRole("button", { name: "刷新 AI 用量" }));
    await waitFor(() =>
      expect(screen.queryByText("a***@example.com")).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("10.50 credits")).not.toBeInTheDocument();
    expect(screen.queryByText("88%")).not.toBeInTheDocument();
    expect(screen.getByText("基金机会日报")).toBeVisible();
  },
);

it.each([true, false])(
  "never carries private account data or an in-flight manual response across user identities (success: %s)",
  async (succeeded) => {
    const login = (id: string) =>
      installLogin({
        accessToken: `test-access-${id}`,
        tokenType: "Bearer",
        expiresIn: 3600,
        user: {
          id,
          nickname: id,
          email: `${id}@example.test`,
          tenantId: `tenant-${id}`,
          tenantName: id,
          role: "OWNER",
          platformRoles: [],
        },
      });
    await login("first");
    let resolveOldRefresh: (value: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.includes("refresh_account=true")) {
          return new Promise<Response>((resolve) => {
            resolveOldRefresh = resolve;
          });
        }
        if (!url.includes("view=account")) return response(aiUsageFixture);
        const firstUser =
          new Headers(init?.headers).get("Authorization") ===
          "Bearer test-access-first";
        return response({
          account: firstUser
            ? aiUsageFixture.account
            : {
                ...aiUsageFixture.account,
                status: "RESTRICTED",
                email: null,
                planType: null,
                authType: null,
                windows: [],
                credits: null,
              },
        });
      }),
    );
    renderWithSession(<OperationsPage />);
    await userEvent.click(screen.getByRole("tab", { name: "AI 用量" }));
    await screen.findByText("a***@example.com");
    await userEvent.click(screen.getByRole("button", { name: "刷新 AI 用量" }));
    await act(async () => {
      await login("second");
    });
    expect(screen.queryByText("a***@example.com")).not.toBeInTheDocument();
    expect(
      await screen.findByText(/仅平台管理员可查看共享 AI 账户/),
    ).toBeVisible();
    await act(async () => {
      resolveOldRefresh(
        succeeded
          ? response({ account: aiUsageFixture.account })
          : response({}, 503),
      );
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "刷新 AI 用量" }),
      ).toBeEnabled(),
    );
    expect(screen.queryByText("a***@example.com")).not.toBeInTheDocument();
    expect(screen.queryByText("10.50 credits")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("alert", { name: "AI 账户加载失败" }),
    ).not.toBeInTheDocument();
  },
);

it("shows missing tokens and reset quota as unknown instead of zero and explains account restrictions", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
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
  let taskRequests = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("view=account"))
        return response({ account: aiUsageFixture.account });
      taskRequests += 1;
      return taskRequests === 1
        ? new Response(
            JSON.stringify({ error: { message: "额度服务暂不可用" } }),
            { status: 503 },
          )
        : response({ ...aiUsageFixture, tasks: [], total: 0 });
    }),
  );
  renderWithSession(<OperationsPage />);
  await user.click(screen.getByRole("tab", { name: "AI 用量" }));
  expect(
    await screen.findByRole("alert", { name: "AI 用量加载失败" }),
  ).toHaveTextContent("额度服务暂不可用");
  expect(screen.getByText("a***@example.com")).toBeVisible();
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
    vi.fn(async () =>
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
    vi.fn(async () =>
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

import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpError } from "../../api/client";
import type { TiantianImportDetail } from "../../api/types";
import { renderWithSession } from "../../test/render-with-session";
import TiantianImportWizard from "./components/TiantianImportWizard";

const mocks = vi.hoisted(() => ({
  preview: vi.fn(),
  confirm: vi.fn(),
  cancel: vi.fn(),
  retry: vi.fn(),
  refetch: vi.fn(),
  detailHook: vi.fn(),
  historyHook: vi.fn(),
  role: { value: "EDITOR" },
}));

vi.mock("../../app/auth-session", () => ({
  useAuthSession: () => ({ role: mocks.role.value }),
}));

vi.mock("../../api/hooks", () => ({
  usePreviewTiantianImport: () => ({
    mutateAsync: mocks.preview,
    isPending: false,
  }),
  useConfirmTiantianImport: () => ({
    mutateAsync: mocks.confirm,
    isPending: false,
  }),
  useCancelTiantianImport: () => ({
    mutateAsync: mocks.cancel,
    isPending: false,
  }),
  useRetryTiantianMetrics: () => ({
    mutateAsync: mocks.retry,
    isPending: false,
  }),
  useTiantianImport: (batchId: string | null, pollMetrics: boolean) =>
    mocks.detailHook(batchId, pollMetrics),
  useTiantianImports: (options?: { status?: "DRAFT" }) =>
    mocks.historyHook(options),
}));

function preview(
  overrides: Partial<TiantianImportDetail> = {},
): TiantianImportDetail {
  return {
    batchId: "batch-1",
    status: "DRAFT",
    version: 1,
    package: {
      schemaVersion: "wealthsail-tiantian-import/v2",
      collectorVersion: "0.2.0",
      positionsAsOf: "2026-08-28T08:00:00+08:00",
      sourceWindowStart: "2020-01-01",
      sourceWindowEnd: "2026-08-28",
    },
    sourceBinding: {
      bindingId: "binding-1",
      status: "PENDING",
      accountId: "account-1",
      displayName: "天天基金账户",
    },
    summary: {
      total: 4,
      new: 1,
      unchanged: 0,
      changed: 1,
      closed: 1,
      conflict: 1,
    },
    positions: [
      {
        itemId: "new-1",
        instrumentId: "fund-new",
        symbol: "110022",
        name: "测试新增基金",
        quantity: "100.00",
        unitCost: "1.20",
        marketValue: "130.00",
        comparisonStatus: "NEW",
        candidateHoldingStartedOn: "2024-01-02",
        holdingStartStatus: "RECONCILED",
        holdingStartMethod: "TRANSACTION_RECONCILED",
        holdingStartConfidence: "HIGH",
        holdingStartReasons: [],
      },
      {
        itemId: "changed-1",
        instrumentId: "fund-changed",
        symbol: "000001",
        name: "测试变化基金",
        quantity: "80.00",
        unitCost: "1.10",
        marketValue: "90.00",
        comparisonStatus: "CHANGED",
        candidateHoldingStartedOn: "2021-01-26",
        holdingStartStatus: "PARTIAL_HISTORY",
        holdingStartMethod: "PORTFOLIO_ACTIVITY_ESTIMATE",
        holdingStartConfidence: "LOW",
        holdingStartReasons: [
          "PORTFOLIO_ACTIVITY_ACCOUNT_MATCH",
          "PORTFOLIO_COMPONENT_DETAILS_UNAVAILABLE",
          "PORTFOLIO_REBALANCE_PRESENT",
        ],
      },
      {
        itemId: "closed-1",
        instrumentId: "fund-closed",
        symbol: "000002",
        name: "测试清仓基金",
        quantity: "0",
        unitCost: "1.00",
        marketValue: null,
        comparisonStatus: "CLOSED",
        candidateHoldingStartedOn: null,
        holdingStartStatus: "INSUFFICIENT_DATA",
        holdingStartMethod: "INSUFFICIENT_DATA",
        holdingStartConfidence: "NONE",
        holdingStartReasons: ["NO_SHARE_TRANSACTIONS"],
      },
      {
        itemId: "conflict-1",
        instrumentId: "fund-conflict",
        symbol: "000003",
        name: "测试冲突基金",
        quantity: "10.00",
        unitCost: "1.00",
        marketValue: "10.00",
        comparisonStatus: "CONFLICT",
        candidateHoldingStartedOn: null,
        holdingStartStatus: "MISMATCH",
        holdingStartMethod: "MISMATCH",
        holdingStartConfidence: "NONE",
        holdingStartReasons: ["SIMULATED_SHARES_MISMATCH"],
      },
    ],
    historySummary: { importable: 16, duplicated: 2, reviewRequired: 3 },
    excludedSummary: { cashManagement: 1545, nonPosting: 8 },
    warnings: ["PARTIAL_HISTORY"],
    blockingErrors: ["ACCOUNT_LEDGER_CONFLICT"],
    validationStatus: "INVALID",
    freshness: "FRESH",
    freshnessReasons: [],
    availableActions: ["VIEW", "CONFIRM", "CANCEL", "REPARSE"],
    terminalReason: null,
    supersedesBatchId: null,
    canConfirm: false,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.preview.mockResolvedValue(preview());
  mocks.role.value = "EDITOR";
  mocks.detailHook.mockReturnValue({ data: undefined, refetch: mocks.refetch });
  mocks.historyHook.mockImplementation(() => ({
    data: { items: [], nextCursor: null },
    isLoading: false,
    error: null,
  }));
});

it("presents empty server import history as a compact guided state", () => {
  renderWithSession(<TiantianImportWizard />);

  expect(
    screen.getByRole("status", { name: "暂无天天基金导入记录" }),
  ).toHaveTextContent("确认写入持仓基线后，导入批次会显示在这里");
});

it("shows pending drafts before upload without asking for the ZIP again", () => {
  const draft = preview({
    blockingErrors: [],
    validationStatus: "VALID",
    canConfirm: true,
  });
  mocks.historyHook.mockImplementation((options?: { status?: "DRAFT" }) => ({
    data: {
      items:
        options?.status === "DRAFT"
          ? [
              {
                batchId: draft.batchId,
                status: "DRAFT",
                version: draft.version,
                sourceAsOf: "2026-08-28T08:00:00+08:00",
                summary: draft.summary,
                validationStatus: "VALID",
                freshness: "FRESH",
                freshnessReasons: [],
                availableActions: ["VIEW", "CONFIRM", "CANCEL", "REPARSE"],
                terminalReason: null,
                supersedesBatchId: null,
                account: { accountId: "account-1", name: "待确认天天基金账户" },
                createdAt: "2026-08-28T08:01:00+08:00",
                updatedAt: "2026-08-29T08:02:00+08:00",
              },
            ]
          : [],
      nextCursor: null,
    },
    isLoading: false,
    error: null,
  }));

  renderWithSession(<TiantianImportWizard />);

  const pendingHeading = screen.getByRole("heading", { name: "待处理草稿" });
  const uploadHeading = screen.getByRole("heading", {
    name: "导入或更新天天基金持仓",
  });
  expect(
    pendingHeading.compareDocumentPosition(uploadHeading) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    screen.getByRole("button", { name: /继续核对.*待确认天天基金账户/ }),
  ).toBeEnabled();
  expect(screen.getByText("有效，可继续确认")).toBeInTheDocument();
});

it("blocks a stale draft and guides an explicit reparse with the original ZIP", async () => {
  const staleDraft = preview({
    blockingErrors: [],
    validationStatus: "VALID",
    freshness: "STALE",
    freshnessReasons: ["PREVIEW_ENGINE_CHANGED"],
    availableActions: ["VIEW", "CANCEL", "REPARSE"],
    canConfirm: false,
  });
  const draftItem = {
    batchId: staleDraft.batchId,
    status: "DRAFT" as const,
    version: staleDraft.version,
    sourceAsOf: "2026-08-28T08:00:00+08:00",
    summary: staleDraft.summary,
    validationStatus: "VALID" as const,
    freshness: "STALE" as const,
    freshnessReasons: ["PREVIEW_ENGINE_CHANGED" as const],
    availableActions: ["VIEW" as const, "CANCEL" as const, "REPARSE" as const],
    terminalReason: null,
    supersedesBatchId: null,
    account: { accountId: "account-1", name: "过期待处理账户" },
    createdAt: "2026-08-28T08:01:00+08:00",
    updatedAt: "2026-08-29T08:02:00+08:00",
  };
  mocks.historyHook.mockImplementation((options?: { status?: "DRAFT" }) => ({
    data: {
      items: options?.status === "DRAFT" ? [draftItem] : [],
      nextCursor: null,
    },
    isLoading: false,
    error: null,
  }));
  mocks.detailHook.mockImplementation((batchId: string | null) => ({
    data: batchId === staleDraft.batchId ? staleDraft : undefined,
    refetch: mocks.refetch,
  }));
  mocks.preview.mockResolvedValue(
    preview({
      previewDisposition: "REPARSED",
      blockingErrors: [],
      validationStatus: "VALID",
      canConfirm: true,
    }),
  );
  const user = userEvent.setup();
  renderWithSession(<TiantianImportWizard />);

  await user.click(
    screen.getByRole("button", { name: /继续核对.*过期待处理账户/ }),
  );
  const dialog = await screen.findByRole("dialog", {
    name: "导入或更新天天基金持仓",
  });
  expect(
    within(dialog).getByText("预览规则已经升级，需要重新解析"),
  ).toBeVisible();
  expect(
    within(dialog).getByRole("button", { name: "确认写入持仓基线" }),
  ).toBeDisabled();

  await user.click(
    within(dialog).getByRole("button", { name: "选择原 ZIP 重新解析" }),
  );
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByText(/正在替代“过期待处理账户”的旧草稿/)).toBeVisible();
  await user.upload(
    screen.getByLabelText("天天基金 ZIP 导入包"),
    new File(["PK\u0003\u0004payload"], "snapshot.zip", {
      type: "application/zip",
    }),
  );
  await user.click(screen.getByRole("button", { name: "重新解析并预览" }));

  expect(mocks.preview).toHaveBeenCalledWith(
    expect.objectContaining({ duplicatePolicy: "REPARSE" }),
  );
});

it("restores a server-side draft from import history instead of relying on browser state", async () => {
  const restoredDraft = preview({ blockingErrors: [], canConfirm: true });
  mocks.historyHook.mockReturnValue({
    data: {
      items: [
        {
          batchId: restoredDraft.batchId,
          status: "DRAFT",
          version: restoredDraft.version,
          sourceAsOf: "2026-08-28T08:00:00+08:00",
          summary: restoredDraft.summary,
          validationStatus: "VALID",
          account: { accountId: "account-1", name: "可恢复天天基金账户" },
          createdAt: "2026-08-28T08:01:00+08:00",
        },
      ],
      nextCursor: null,
    },
    isLoading: false,
    error: null,
  });
  mocks.detailHook.mockImplementation((batchId: string | null) => ({
    data: batchId === restoredDraft.batchId ? restoredDraft : undefined,
    refetch: mocks.refetch,
  }));
  const user = userEvent.setup();
  renderWithSession(<TiantianImportWizard />);

  await user.click(
    screen.getByRole("button", { name: /可恢复天天基金账户.*DRAFT/ }),
  );

  expect(
    await screen.findByRole("dialog", { name: "导入或更新天天基金持仓" }),
  ).toBeInTheDocument();
  expect(mocks.detailHook).toHaveBeenCalledWith("batch-1", undefined);
});

it("renders complete preview counts, three tabs and blocks conflicts", async () => {
  const user = userEvent.setup();
  renderWithSession(<TiantianImportWizard />);

  await user.upload(
    screen.getByLabelText("天天基金 ZIP 导入包"),
    new File(["PK\u0003\u0004payload"], "snapshot.zip", {
      type: "application/zip",
    }),
  );
  await user.click(screen.getByRole("button", { name: "解析并预览" }));

  const dialog = await screen.findByRole("dialog", {
    name: "导入或更新天天基金持仓",
  });
  expect(within(dialog).getByText("持仓 4")).toBeInTheDocument();
  expect(within(dialog).getByText("新增 1")).toBeInTheDocument();
  expect(within(dialog).getByText("变化 1")).toBeInTheDocument();
  expect(within(dialog).getByText("清仓 1")).toBeInTheDocument();
  expect(within(dialog).getByText("冲突 1")).toBeInTheDocument();
  expect(within(dialog).getAllByRole("tab")).toHaveLength(3);
  expect(within(dialog).getByText("测试新增基金")).toBeInTheDocument();
  expect(within(dialog).getByText("新增")).toBeInTheDocument();
  expect(within(dialog).getByText("有变化")).toBeInTheDocument();
  expect(within(dialog).getByText("将清仓")).toBeInTheDocument();
  expect(within(dialog).getByText("冲突")).toBeInTheDocument();

  await user.click(within(dialog).getByRole("tab", { name: "历史流水" }));
  expect(within(dialog).getByText("可导入 16 条")).toBeInTheDocument();
  expect(within(dialog).getByText("重复 2 条")).toBeInTheDocument();
  expect(within(dialog).getByText("排除活期宝 1545 条")).toBeInTheDocument();
  expect(
    within(dialog).getByText("数据范围 2020-01-01 至 2026-08-28"),
  ).toBeInTheDocument();
  await user.click(within(dialog).getByRole("tab", { name: "问题与提示" }));
  expect(
    within(dialog).getByText(
      "目标账户中存在其他来源的持仓事实，需要先处理账户冲突",
    ),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole("button", { name: "确认写入持仓基线" }),
  ).toBeDisabled();
});

it("prefills inferred dates and explains their method and confidence", async () => {
  mocks.preview.mockResolvedValue(
    preview({ blockingErrors: [], warnings: [], canConfirm: true }),
  );
  const user = userEvent.setup();
  renderWithSession(<TiantianImportWizard />);

  await user.upload(
    screen.getByLabelText("天天基金 ZIP 导入包"),
    new File(["PK\u0003\u0004payload"], "snapshot.zip", {
      type: "application/zip",
    }),
  );
  await user.click(screen.getByRole("button", { name: "解析并预览" }));

  const dialog = await screen.findByRole("dialog", {
    name: "导入或更新天天基金持仓",
  });
  expect(within(dialog).getByLabelText("测试新增基金持有开始日期")).toHaveValue(
    "2024-01-02",
  );
  expect(within(dialog).getByLabelText("测试变化基金持有开始日期")).toHaveValue(
    "2021-01-26",
  );
  expect(
    within(dialog).getByText("逐基金交易流水 · 高可信"),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText("组合/调仓估算 · 低可信"),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText("组合调仓可能改变成分，建议核对"),
  ).toBeInTheDocument();
});

it("keeps an unresolved fund visible and explains the blocking conflict", async () => {
  mocks.preview.mockResolvedValue(
    preview({
      summary: {
        total: 1,
        new: 0,
        unchanged: 0,
        changed: 0,
        closed: 0,
        conflict: 1,
      },
      positions: [
        {
          itemId: "unresolved:holding-999999",
          instrumentId: null,
          symbol: "999999",
          name: "来源基金名称",
          quantity: "100.00",
          unitCost: "1.20",
          marketValue: "130.00",
          comparisonStatus: "CONFLICT",
          candidateHoldingStartedOn: null,
          holdingStartStatus: "INSUFFICIENT_DATA",
          holdingStartMethod: "INSUFFICIENT_DATA",
          holdingStartConfidence: "NONE",
          holdingStartReasons: ["NO_SHARE_TRANSACTIONS"],
          validationStatus: "INVALID",
          validationErrors: ["UNKNOWN_FUND_CODE"],
        },
      ],
      blockingErrors: ["UNKNOWN_FUND_CODE"],
      warnings: [],
      canConfirm: false,
    }),
  );
  const user = userEvent.setup();
  renderWithSession(<TiantianImportWizard />);

  await user.upload(
    screen.getByLabelText("天天基金 ZIP 导入包"),
    new File(["PK\u0003\u0004payload"], "snapshot.zip", {
      type: "application/zip",
    }),
  );
  await user.click(screen.getByRole("button", { name: "解析并预览" }));

  const dialog = await screen.findByRole("dialog", {
    name: "导入或更新天天基金持仓",
  });
  expect(within(dialog).getByText("持仓 1")).toBeInTheDocument();
  expect(within(dialog).getByText("冲突 1")).toBeInTheDocument();
  expect(within(dialog).getByText("来源基金名称")).toBeInTheDocument();
  expect(within(dialog).getByText("999999")).toBeInTheDocument();
  expect(within(dialog).getByText("份额 100.00")).toBeInTheDocument();
  expect(
    within(dialog).getByText("基金代码未通过权威主数据核验，暂不能导入"),
  ).toBeInTheDocument();

  await user.click(within(dialog).getByRole("tab", { name: "问题与提示" }));
  expect(
    within(dialog).getByText("部分基金代码未通过权威主数据核验，请稍后重试"),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole("button", { name: "确认写入持仓基线" }),
  ).toBeDisabled();
});

it("submits reviewed dates and explicit empty-snapshot consent only once", async () => {
  let resolveConfirm!: (value: unknown) => void;
  mocks.preview.mockResolvedValue(
    preview({
      canConfirm: true,
      summary: {
        total: 1,
        new: 0,
        unchanged: 0,
        changed: 0,
        closed: 1,
        conflict: 0,
      },
      positions: [preview().positions[2]],
      blockingErrors: [],
    }),
  );
  mocks.confirm.mockReturnValue(
    new Promise((resolve) => {
      resolveConfirm = resolve;
    }),
  );
  const user = userEvent.setup();
  renderWithSession(<TiantianImportWizard />);
  await user.upload(
    screen.getByLabelText("天天基金 ZIP 导入包"),
    new File(["PK\u0003\u0004payload"], "snapshot.zip", {
      type: "application/zip",
    }),
  );
  await user.click(screen.getByRole("button", { name: "解析并预览" }));
  const dialog = await screen.findByRole("dialog", {
    name: "导入或更新天天基金持仓",
  });
  await user.type(
    within(dialog).getByLabelText("测试清仓基金持有开始日期"),
    "2024-02-03",
  );
  await user.click(
    within(dialog).getByRole("checkbox", {
      name: "我已核对当前持仓，并确认使用该快照更新天天基金账户",
    }),
  );
  await user.click(
    within(dialog).getByRole("checkbox", {
      name: "我确认本次快照会清空该账户的全部基金持仓",
    }),
  );
  await user.dblClick(
    within(dialog).getByRole("button", { name: "确认写入持仓基线" }),
  );

  expect(mocks.confirm).toHaveBeenCalledTimes(1);
  expect(mocks.confirm).toHaveBeenCalledWith(
    expect.objectContaining({
      batchId: "batch-1",
      version: 1,
      payload: {
        decisions: [{ itemId: "closed-1", startedOn: "2024-02-03" }],
        confirmEmpty: true,
      },
    }),
  );
  await act(async () => {
    resolveConfirm({
      batchId: "batch-1",
      status: "CONFIRMED",
      version: 2,
      metricsStatus: "PENDING",
      positionCount: 0,
      historyImported: 16,
      historyDuplicated: 2,
    });
  });
  expect(await within(dialog).findByText("账本基线已确认")).toBeInTheDocument();
  expect(within(dialog).getByText("区间指标正在后台计算")).toBeInTheDocument();
});

it("validates ZIP type and 20 MB before upload, and accepts drag and drop", () => {
  renderWithSession(<TiantianImportWizard />);
  const input = screen.getByLabelText("天天基金 ZIP 导入包");

  fireEvent.change(input, {
    target: {
      files: [new File(["not zip"], "holdings.txt", { type: "text/plain" })],
    },
  });
  expect(screen.getByRole("alert")).toHaveTextContent("请选择 .zip 导入包");
  expect(screen.getByRole("button", { name: "解析并预览" })).toBeDisabled();

  fireEvent.change(input, {
    target: {
      files: [
        new File([new Uint8Array(20 * 1024 * 1024 + 1)], "too-large.zip", {
          type: "application/zip",
        }),
      ],
    },
  });
  expect(screen.getByRole("alert")).toHaveTextContent("不能超过 20 MB");

  const valid = new File(["PK\u0003\u0004payload"], "snapshot.zip", {
    type: "application/zip",
  });
  fireEvent.drop(screen.getByTestId("tiantian-drop-zone"), {
    dataTransfer: { files: [valid] },
  });
  expect(screen.getByText(/已选择：snapshot.zip/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "解析并预览" })).toBeEnabled();
});

it("keeps viewer access read-only before a package can be selected", () => {
  mocks.role.value = "VIEWER";
  renderWithSession(<TiantianImportWizard />);

  expect(
    screen.getByText("当前角色只能查看持仓和导入历史"),
  ).toBeInTheDocument();
  expect(
    screen.queryByLabelText("天天基金 ZIP 导入包"),
  ).not.toBeInTheDocument();
});

it("can target an existing account and reuses the preview command key after transport failure", async () => {
  mocks.historyHook.mockReturnValue({
    data: {
      items: [
        {
          batchId: "old-batch",
          status: "CONFIRMED",
          version: 2,
          sourceAsOf: "2026-08-20T08:00:00+08:00",
          summary: preview().summary,
          validationStatus: "VALID",
          account: { accountId: "account-existing", name: "已有天天基金账户" },
          createdAt: "2026-08-20T08:01:00+08:00",
        },
      ],
      nextCursor: null,
    },
    isLoading: false,
    error: null,
  });
  mocks.preview
    .mockRejectedValueOnce(new HttpError("网络暂时中断", 503, "UPSTREAM_ERROR"))
    .mockResolvedValueOnce(preview());
  const user = userEvent.setup();
  renderWithSession(<TiantianImportWizard />);

  await user.selectOptions(
    screen.getByLabelText("目标 WealthSail 账户"),
    "account-existing",
  );
  expect(
    screen.queryByLabelText("WealthSail 账户名称"),
  ).not.toBeInTheDocument();
  await user.upload(
    screen.getByLabelText("天天基金 ZIP 导入包"),
    new File(["PK\u0003\u0004payload"], "snapshot.zip", {
      type: "application/zip",
    }),
  );
  await user.click(screen.getByRole("button", { name: "解析并预览" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("网络暂时中断");
  await user.click(screen.getByRole("button", { name: "解析并预览" }));
  await screen.findByRole("dialog", { name: "导入或更新天天基金持仓" });

  expect(mocks.preview).toHaveBeenCalledTimes(2);
  expect(mocks.preview.mock.calls[0][0]).toMatchObject({
    accountId: "account-existing",
    accountName: undefined,
  });
  expect(mocks.preview.mock.calls[1][0].idempotencyKey).toBe(
    mocks.preview.mock.calls[0][0].idempotencyKey,
  );
});

it.each([
  { metricsStatus: "FAILED" as const, buttonName: "重试区间指标" },
  { metricsStatus: "PARTIAL" as const, buttonName: "重新计算区间指标" },
  { metricsStatus: "CONFLICT" as const, buttonName: "重新计算区间指标" },
  { metricsStatus: "READY" as const, buttonName: "重新计算区间指标" },
])(
  "keeps the confirmed ledger intact when $metricsStatus metrics are requeued",
  async ({ metricsStatus, buttonName }) => {
    const confirmed = preview({
      status: "CONFIRMED",
      version: 2,
      canConfirm: false,
      metricsStatus,
    });
    mocks.historyHook.mockReturnValue({
      data: {
        items: [
          {
            batchId: confirmed.batchId,
            status: "CONFIRMED",
            version: 2,
            sourceAsOf: "2026-08-28T08:00:00+08:00",
            summary: confirmed.summary,
            validationStatus: "VALID",
            account: { accountId: "account-1", name: "已确认账户" },
            createdAt: "2026-08-28T08:01:00+08:00",
          },
        ],
        nextCursor: null,
      },
      isLoading: false,
      error: null,
    });
    mocks.detailHook.mockImplementation((batchId: string | null) => ({
      data: batchId === confirmed.batchId ? confirmed : undefined,
      refetch: mocks.refetch,
      isLoading: false,
      error: null,
    }));
    mocks.retry.mockResolvedValue({
      batchId: confirmed.batchId,
      metricsStatus: "PENDING",
    });
    const user = userEvent.setup();
    renderWithSession(<TiantianImportWizard />);

    await user.click(
      screen.getByRole("button", { name: /已确认账户.*CONFIRMED/ }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "导入或更新天天基金持仓",
    });
    expect(within(dialog).getByText("账本基线已确认")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: buttonName }));

    expect(mocks.retry).toHaveBeenCalledWith(
      expect.objectContaining({ batchId: "batch-1" }),
    );
    expect(mocks.confirm).not.toHaveBeenCalled();
  },
);

it("keeps metric recalculation read-only for a viewer", async () => {
  mocks.role.value = "VIEWER";
  const confirmed = preview({
    status: "CONFIRMED",
    version: 2,
    canConfirm: false,
    metricsStatus: "PARTIAL",
  });
  mocks.historyHook.mockReturnValue({
    data: {
      items: [
        {
          batchId: confirmed.batchId,
          status: "CONFIRMED",
          version: 2,
          sourceAsOf: "2026-08-28T08:00:00+08:00",
          summary: confirmed.summary,
          validationStatus: "VALID",
          account: { accountId: "account-1", name: "只读账户" },
          createdAt: "2026-08-28T08:01:00+08:00",
        },
      ],
      nextCursor: null,
    },
    isLoading: false,
    error: null,
  });
  mocks.detailHook.mockImplementation((batchId: string | null) => ({
    data: batchId === confirmed.batchId ? confirmed : undefined,
    refetch: mocks.refetch,
    isLoading: false,
    error: null,
  }));
  const user = userEvent.setup();
  renderWithSession(<TiantianImportWizard />);

  await user.click(screen.getByRole("button", { name: /只读账户.*CONFIRMED/ }));
  const dialog = await screen.findByRole("dialog", {
    name: "导入或更新天天基金持仓",
  });

  expect(
    within(dialog).queryByRole("button", { name: "重新计算区间指标" }),
  ).not.toBeInTheDocument();
});

it("turns a stale confirmation into an explicit refresh path", async () => {
  mocks.preview.mockResolvedValue(
    preview({ canConfirm: true, blockingErrors: [] }),
  );
  mocks.confirm.mockRejectedValue(
    new HttpError("预览版本已经变化", 409, "VERSION_CONFLICT", "req-stale"),
  );
  const user = userEvent.setup();
  renderWithSession(<TiantianImportWizard />);
  await user.upload(
    screen.getByLabelText("天天基金 ZIP 导入包"),
    new File(["PK\u0003\u0004payload"], "snapshot.zip", {
      type: "application/zip",
    }),
  );
  await user.click(screen.getByRole("button", { name: "解析并预览" }));
  const dialog = await screen.findByRole("dialog", {
    name: "导入或更新天天基金持仓",
  });
  await user.click(
    within(dialog).getByRole("checkbox", {
      name: "我已核对当前持仓，并确认使用该快照更新天天基金账户",
    }),
  );
  await user.click(
    within(dialog).getByRole("button", { name: "确认写入持仓基线" }),
  );

  expect(
    await within(dialog).findByText("预览已经变化，请刷新后重新确认"),
  ).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "刷新预览" }));
  expect(mocks.refetch).toHaveBeenCalledTimes(1);
});

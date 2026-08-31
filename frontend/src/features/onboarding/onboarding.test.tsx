import { screen, within } from "@testing-library/react";
import { beforeEach, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import OnboardingPage from "./OnboardingPage";
import { renderWithSession } from "../../test/render-with-session";

const mocks = vi.hoisted(() => ({
  submitCsv: vi.fn(),
  role: null as null | "OWNER" | "ADMIN" | "EDITOR" | "VIEWER",
}));

vi.mock("../../app/auth-session", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../app/auth-session")>()),
  useAuthSession: () =>
    mocks.role
      ? {
          userId: "user-1",
          displayName: "测试用户",
          email: "user@example.com",
          tenantId: "tenant-1",
          tenantName: "测试租户",
          role: mocks.role,
          platformRoles: [],
          accessToken: "access-token",
          expiresIn: 900,
        }
      : null,
}));

vi.mock("../../api/hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/hooks")>()),
  useCsvPositionImport: () => ({
    mutateAsync: mocks.submitCsv,
    isPending: false,
  }),
}));

beforeEach(() => {
  mocks.role = "EDITOR";
  mocks.submitCsv.mockReset();
  mocks.submitCsv.mockResolvedValue({
    batchId: "csv-batch-1",
    status: "CONFIRMED",
    version: 2,
    importedCount: 1,
    eventIds: ["event-1"],
    replayed: false,
  });
});

it("keeps CSV import read-only for a VIEWER", async () => {
  mocks.role = "VIEWER";
  const user = userEvent.setup();
  renderWithSession(<OnboardingPage />);

  await user.click(screen.getByRole("button", { name: "校验并预览" }));

  const dialog = screen.getByRole("dialog", { name: "批次导入预览" });
  expect(
    within(dialog).getByRole("button", { name: "批次提交" }),
  ).toBeDisabled();
  expect(mocks.submitCsv).not.toHaveBeenCalled();
});

it("blocks the whole CSV batch while any preview row is invalid", async () => {
  const user = userEvent.setup();
  renderWithSession(<OnboardingPage />);

  await user.clear(screen.getByLabelText("导入数据"));
  await user.type(
    screen.getByLabelText("导入数据"),
    "STOCK,600036,招商银行,1000,38.82\nFUND,,缺少代码,2000,1.25",
  );
  await user.click(screen.getByRole("button", { name: "校验并预览" }));

  const dialog = screen.getByRole("dialog", { name: "批次导入预览" });
  expect(within(dialog).getByText("1 条可导入")).toBeInTheDocument();
  expect(within(dialog).getByText("1 条需修正")).toBeInTheDocument();
  expect(
    within(dialog).getByRole("button", { name: "批次提交" }),
  ).toBeDisabled();
  expect(mocks.submitCsv).not.toHaveBeenCalled();
  expect(screen.queryByText(/已导入 .* 条有效记录/)).not.toBeInTheDocument();
});

it("submits a valid CSV batch and displays the confirmed result", async () => {
  const user = userEvent.setup();
  renderWithSession(<OnboardingPage />);

  await user.clear(screen.getByLabelText("导入数据"));
  await user.type(
    screen.getByLabelText("导入数据"),
    "STOCK,600036,招商银行,8500,38.82",
  );
  await user.click(screen.getByRole("button", { name: "校验并预览" }));
  await user.click(screen.getByRole("button", { name: "批次提交" }));

  expect(mocks.submitCsv).toHaveBeenCalledWith(
    expect.objectContaining({
      csvData: "STOCK,600036,招商银行,8500,38.82",
      idempotencyKey: expect.stringMatching(/^csv-import-/),
    }),
  );
  expect(await screen.findByText("已导入 1 条有效记录")).toBeInTheDocument();
  expect(screen.getByText("批次 csv-batch-1")).toBeInTheDocument();
});

it("presents CSV history as pending until the first confirmed batch", () => {
  renderWithSession(<OnboardingPage />);

  const emptyHistory = screen.getByRole("status", {
    name: "暂无 CSV 批次记录",
  });
  expect(emptyHistory).toHaveTextContent(
    "成功提交 CSV 后，最近一次确认结果会显示在这里。",
  );
});

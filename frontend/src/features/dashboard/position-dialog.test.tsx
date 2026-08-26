import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PositionDialog from "./components/PositionDialog";
import { renderWithSession } from "../../test/render-with-session";

const addMutate = vi.fn<(payload: unknown) => Promise<unknown>>(async () => ({
  batchId: "batch-1",
  status: "DRAFT",
  version: 1,
  item: {
    itemId: "item-1",
    symbol: "600519",
    name: "贵州茅台",
    assetType: "STOCK",
    quantity: "100",
    unitCost: "1500.00",
    tradeDate: "2026-08-26",
    accountId: "account-1",
    validationStatus: "VALID",
  },
}));
const confirmMutate = vi.fn<(input: unknown) => Promise<unknown>>(async () => ({
  batchId: "batch-1",
  status: "CONFIRMED",
  version: 2,
  eventId: "event-1",
}));
vi.mock("../../api/hooks", () => ({
  useAddPosition: () => ({ mutateAsync: addMutate, isPending: false }),
  useConfirmPositionImport: () => ({ mutateAsync: confirmMutate, isPending: false }),
  useInstrumentSearch: (keyword: string) => ({
    data:
      keyword.includes("600519") || keyword.includes("茅台")
        ? [{ symbol: "600519", name: "贵州茅台", market: "SSE", type: "STOCK" as const }]
        : [],
    isLoading: false,
    isError: false,
    error: null,
  }),
}));

describe("PositionDialog", () => {
  beforeEach(() => {
    addMutate.mockClear();
    confirmMutate.mockClear();
  });

  it("添加模式：先创建草稿预览，再由用户确认期初持仓", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithSession(
      <PositionDialog open onClose={onClose} />,
    );

    expect(screen.getByRole("dialog", { name: "初始化一笔持仓" })).toBeInTheDocument();

    // 中文关键词用 change 直接注入（userEvent 逐键模拟对 IME 不可靠）
    fireEvent.change(screen.getByLabelText(/搜索标的/), {
      target: { value: "茅台" },
    });
    await waitFor(() => expect(screen.getByText("贵州茅台")).toBeInTheDocument());
    await user.click(screen.getByRole("option", { name: /600519/ }));

    fireEvent.change(screen.getByLabelText(/数量/), {
      target: { value: "100" },
    });
    fireEvent.change(screen.getByLabelText(/成本价/), {
      target: { value: "1500.00" },
    });
    await user.click(screen.getByRole("button", { name: /创建草稿并预览/ }));

    await waitFor(() => expect(addMutate).toHaveBeenCalled());
    const command = addMutate.mock.calls[0]?.[0] as unknown as {
      payload: {
        symbol: string;
        assetType: string;
        quantity: string;
        avgCost: string;
      };
      idempotencyKey: string;
    };
    expect(command.payload).toMatchObject({
      symbol: "600519",
      assetType: "STOCK",
      quantity: "100",
      avgCost: "1500.00",
    });
    expect(command.idempotencyKey).toMatch(/[0-9a-f-]{20,}/i);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText(/尚未写入事实账本/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /确认期初持仓/ }));
    await waitFor(() =>
      expect(confirmMutate).toHaveBeenCalledWith(
        expect.objectContaining({ batchId: "batch-1" }),
      ),
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("数量非法时拒绝提交并提示", async () => {
    const user = userEvent.setup();
    renderWithSession(
      <PositionDialog open onClose={vi.fn()} />,
    );
    await user.type(screen.getByLabelText(/数量/), "0");
    await user.type(screen.getByLabelText(/成本价/), "100");
    expect(screen.getByRole("button", { name: /创建草稿并预览/ })).toBeDisabled();
    expect(addMutate).not.toHaveBeenCalled();
  });
});

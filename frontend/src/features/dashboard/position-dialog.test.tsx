import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PositionDialog from "./components/PositionDialog";
import { renderWithSession } from "../../test/render-with-session";
import { positionsFixture } from "../../test/fixtures";

const addMutate = vi.fn<(payload: unknown) => Promise<unknown>>(async () => ({
  position: {
    ...positionsFixture[0],
    avgCost: "1500.00",
    buyDate: "2026-08-26",
    eventId: "evt-1",
  },
}));
const updateMutate = vi.fn<(input: unknown) => Promise<unknown>>(async () => ({
  position: {
    ...positionsFixture[0],
    avgCost: "1400.00",
    buyDate: "2026-08-26",
    eventId: "evt-2",
  },
}));

vi.mock("../../api/hooks", () => ({
  useAddPosition: () => ({ mutateAsync: addMutate, isPending: false }),
  useUpdatePosition: () => ({ mutateAsync: updateMutate, isPending: false }),
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
    updateMutate.mockClear();
  });

  it("添加模式：搜索 → 选择标的 → 填写表单 → 提交添加接口", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithSession(
      <PositionDialog open mode="add" initial={null} onClose={onClose} />,
    );

    expect(screen.getByRole("dialog", { name: "添加持仓" })).toBeInTheDocument();

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
    await user.click(screen.getByRole("button", { name: /确认添加/ }));

    await waitFor(() => expect(addMutate).toHaveBeenCalled());
    const payload = addMutate.mock.calls[0]?.[0] as unknown as {
      symbol: string;
      assetType: string;
      quantity: string;
      avgCost: string;
    };
    expect(payload).toMatchObject({
      symbol: "600519",
      assetType: "STOCK",
      quantity: "100",
      avgCost: "1500.00",
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("编辑模式：预填当前数量/成本，提交更新接口", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderWithSession(
      <PositionDialog open mode="edit" initial={positionsFixture[0]} onClose={onClose} />,
    );

    expect(
      screen.getByRole("dialog", { name: /编辑持仓/ }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/数量/)).toHaveValue("8500");
    expect(screen.getByLabelText(/成本价/)).toHaveValue("28.25");

    fireEvent.change(screen.getByLabelText(/数量/), { target: { value: "9000" } });
    await user.click(screen.getByRole("button", { name: /保存修改/ }));

    await waitFor(() => expect(updateMutate).toHaveBeenCalled());
    const call = updateMutate.mock.calls[0]?.[0] as unknown as {
      symbol: string;
      payload: { quantity: string; avgCost: string };
    };
    expect(call.symbol).toBe("600036");
    expect(call.payload).toMatchObject({ quantity: "9000", avgCost: "28.25" });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("数量非法时拒绝提交并提示", async () => {
    const user = userEvent.setup();
    renderWithSession(
      <PositionDialog open mode="add" initial={null} onClose={vi.fn()} />,
    );
    await user.type(screen.getByLabelText(/数量/), "0");
    await user.type(screen.getByLabelText(/成本价/), "100");
    await fireEvent.click(screen.getByRole("button", { name: /确认添加/ }));
    expect(screen.getByText(/数量必须为大于 0 的数字/)).toBeInTheDocument();
    expect(addMutate).not.toHaveBeenCalled();
  });
});

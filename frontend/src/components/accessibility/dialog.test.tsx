import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Dialog from "./Dialog";

function DialogFixture() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        打开导入
      </button>
      <Dialog open={open} title="导入预览" onClose={() => setOpen(false)}>
        <button type="button">确认导入</button>
      </Dialog>
    </>
  );
}

it("moves focus into the modal, traps tab focus, closes with Escape and restores focus", async () => {
  const user = userEvent.setup();
  render(<DialogFixture />);
  const trigger = screen.getByRole("button", { name: "打开导入" });

  await user.click(trigger);

  const dialog = screen.getByRole("dialog", { name: "导入预览" });
  const close = screen.getByRole("button", { name: "关闭导入预览" });
  const confirm = screen.getByRole("button", { name: "确认导入" });
  expect(dialog).toContainElement(document.activeElement as HTMLElement | null);
  expect(close).toHaveFocus();

  await user.tab({ shift: true });
  expect(confirm).toHaveFocus();
  await user.tab();
  expect(close).toHaveFocus();

  await user.keyboard("{Escape}");
  expect(
    screen.queryByRole("dialog", { name: "导入预览" }),
  ).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

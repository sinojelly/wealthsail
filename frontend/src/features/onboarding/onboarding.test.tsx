import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OnboardingPage from "./OnboardingPage";
import { renderWithSession } from "../../test/render-with-session";

it("previews an import batch and commits only validated rows", async () => {
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
  await user.click(
    within(dialog).getByRole("button", { name: "导入有效记录" }),
  );
  expect(await screen.findByText("已导入 1 条有效记录")).toBeInTheDocument();
});

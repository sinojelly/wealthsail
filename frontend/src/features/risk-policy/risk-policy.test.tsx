import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RiskPolicyPage from "./RiskPolicyPage";
import { renderWithSession } from "../../test/render-with-session";

it("previews a draft through the policy service before a reauthenticated confirmation", async () => {
  const user = userEvent.setup();
  renderWithSession(<RiskPolicyPage />);

  const positionInput = screen.getByLabelText("单一标的最大仓位");
  await user.clear(positionInput);
  await user.type(positionInput, "25");
  expect(screen.getByText("DRAFT")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "预览策略影响" }));
  expect(
    await screen.findByRole("heading", { name: "策略变更预览" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/30.00% → 25.00%/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "确认并启用" }));
  await user.type(screen.getByLabelText("重新验证口令"), "owner-passphrase");
  await user.click(screen.getByRole("button", { name: "确认启用策略" }));
  expect(await screen.findByText("CONFIRMED")).toBeInTheDocument();
});

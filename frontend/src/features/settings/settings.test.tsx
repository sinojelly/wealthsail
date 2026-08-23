import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPage from "./SettingsPage";
import { renderWithSession } from "../../test/render-with-session";

it("completes the enterprise WeCom binding lifecycle and supports soft revocation", async () => {
  const user = userEvent.setup();
  renderWithSession(<SettingsPage />);

  await user.click(screen.getByRole("button", { name: "生成一次性绑定凭证" }));
  expect(await screen.findByText("等待员工确认")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "模拟企业微信确认" }));
  expect(await screen.findByText("已绑定")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "解除绑定" }));
  await user.click(screen.getByRole("button", { name: "确认解除绑定" }));
  expect(await screen.findByText("已撤销")).toBeInTheDocument();
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import LoginPage from "./LoginPage";

it("keeps invited registration disabled until the password reaches eight characters", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  await user.click(screen.getByRole("button", { name: "受邀注册" }));
  await user.type(screen.getByLabelText("邀请令牌"), "WS-VALID-INVITATION-TOKEN");
  await user.type(screen.getByLabelText("邮箱"), "invitee@example.com");

  const password = screen.getByLabelText("登录密码");
  const submit = screen.getByRole("button", { name: "接受邀请并创建账号" });
  expect(screen.getByText("密码要求：至少 8 个字符")).toBeInTheDocument();
  expect(submit).toBeDisabled();
  await user.type(password, "1234567");
  expect(screen.getByText("还需 1 个字符（至少 8 个字符）")).toBeInTheDocument();
  await user.type(password, "8");
  expect(screen.getByText("已满足：至少 8 个字符")).toBeInTheDocument();
  expect(submit).toBeEnabled();
});

it("offers password reset and self registration from the login page", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  expect(screen.getByRole("button", { name: "忘记密码？" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "自助注册" }));
  expect(screen.getByLabelText("租户名称")).toBeInTheDocument();
});

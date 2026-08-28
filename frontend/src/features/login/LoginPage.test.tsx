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

it("offers password reset but not public self registration", () => {
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  expect(screen.getByRole("button", { name: "忘记密码？" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "自助注册" })).not.toBeInTheDocument();
});

it("consumes a reset token from the fragment and immediately scrubs the URL", () => {
  window.history.replaceState({}, "", "/login#reset-token=secret%2Ftoken");
  render(<MemoryRouter><LoginPage /></MemoryRouter>);

  expect(screen.getByLabelText("新密码")).toBeInTheDocument();
  expect(window.location.hash).toBe("");
  expect(window.location.search).toBe("");
});

it("does not submit a reset request until the email is valid", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  await user.click(screen.getByRole("button", { name: "忘记密码？" }));
  const submit = screen.getByRole("button", { name: "发送重置邮件" });
  expect(submit).toBeDisabled();
  await user.type(screen.getByLabelText("邮箱"), "invalid");
  expect(submit).toBeDisabled();
  await user.clear(screen.getByLabelText("邮箱"));
  await user.type(screen.getByLabelText("邮箱"), "user@example.com");
  expect(submit).toBeEnabled();
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { safeReturnTo } from "../../app/navigation";
import LoginPage from "./LoginPage";

const authMocks = vi.hoisted(() => ({
  login: vi.fn(),
}));

vi.mock("../../app/auth-session", () => ({
  login: authMocks.login,
}));

beforeEach(() => {
  authMocks.login.mockReset();
  authMocks.login.mockResolvedValue(undefined);
  window.history.replaceState({}, "", "/login");
});

it("rejects an external post-login redirect", () => {
  expect(safeReturnTo("//attacker.example/reports")).toBe("/dashboard");
  expect(safeReturnTo("https://attacker.example/reports")).toBe("/dashboard");
});

it("keeps invited registration disabled until the password reaches eight characters", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
  await user.click(screen.getByRole("button", { name: "受邀注册" }));
  await user.type(
    screen.getByLabelText("邀请令牌"),
    "WS-VALID-INVITATION-TOKEN",
  );
  await user.type(screen.getByLabelText("邮箱"), "invitee@example.com");

  const password = screen.getByLabelText("登录密码");
  const submit = screen.getByRole("button", { name: "接受邀请并创建账号" });
  expect(screen.getByText("密码要求：至少 8 个字符")).toBeInTheDocument();
  expect(submit).toBeDisabled();
  await user.type(password, "1234567");
  expect(
    screen.getByText("还需 1 个字符（至少 8 个字符）"),
  ).toBeInTheDocument();
  await user.type(password, "8");
  expect(screen.getByText("已满足：至少 8 个字符")).toBeInTheDocument();
  expect(submit).toBeEnabled();
});

it("offers password reset but not public self registration", () => {
  render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("button", { name: "忘记密码？" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "自助注册" }),
  ).not.toBeInTheDocument();
});

it("renders only the red-white scanning beam without a static red track", () => {
  const { container } = render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );

  const beam = container.querySelector("svg.login-beam");
  const paths = beam?.querySelectorAll("path");
  const gradients = beam?.querySelectorAll("linearGradient");
  const haloGradient = beam?.querySelector("#beam-scan-halo-gradient");
  const coreGradient = beam?.querySelector("#beam-scan-core-gradient");
  const gradientMotion = beam?.querySelectorAll(
    'animateTransform[attributeName="gradientTransform"]',
  );
  const sweepingCurve =
    "M -180 620 C 80 230, 340 790, 650 560 C 920 270, 1110 780, 1360 500 C 1510 330, 1590 610, 1720 470";

  expect(paths).toHaveLength(2);
  expect(paths?.[0]).toHaveClass("beam-scan-halo");
  expect(paths?.[0]).toHaveAttribute("stroke", "url(#beam-scan-halo-gradient)");
  expect(paths?.[0]).toHaveAttribute("stroke-width", "6");
  expect(paths?.[1]).toHaveClass("beam-scan-core");
  expect(paths?.[1]).toHaveAttribute("stroke", "url(#beam-scan-core-gradient)");
  expect(paths?.[1]).toHaveAttribute("stroke-width", "1.2");
  expect(paths?.[0]).toHaveAttribute("d", sweepingCurve);
  expect(paths?.[1]).toHaveAttribute("d", sweepingCurve);
  expect(beam?.querySelector(".beam-base-halo")).not.toBeInTheDocument();
  expect(beam?.querySelector(".beam-base-core")).not.toBeInTheDocument();
  expect(beam?.querySelector(".beam-highlight")).not.toBeInTheDocument();
  expect(gradients).toHaveLength(2);
  expect(haloGradient).toHaveAttribute("x1", "-640");
  expect(haloGradient).toHaveAttribute("x2", "360");
  expect(coreGradient).toHaveAttribute("x1", "-640");
  expect(coreGradient).toHaveAttribute("x2", "360");
  expect(gradientMotion).toHaveLength(2);
  expect(gradientMotion?.[0]).toHaveAttribute("dur", "8s");
  expect(gradientMotion?.[0]).toHaveAttribute("repeatCount", "indefinite");
});

it("consumes a reset token from the fragment and immediately scrubs the URL", () => {
  window.history.replaceState({}, "", "/login#reset-token=secret%2Ftoken");
  render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );

  expect(screen.getByLabelText("新密码")).toBeInTheDocument();
  expect(window.location.hash).toBe("");
  expect(window.location.search).toBe("");
});

it("does not submit a reset request until the email is valid", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
  await user.click(screen.getByRole("button", { name: "忘记密码？" }));
  const submit = screen.getByRole("button", { name: "发送重置邮件" });
  expect(submit).toBeDisabled();
  await user.type(screen.getByLabelText("邮箱"), "invalid");
  expect(submit).toBeDisabled();
  await user.clear(screen.getByLabelText("邮箱"));
  await user.type(screen.getByLabelText("邮箱"), "user@example.com");
  expect(submit).toBeEnabled();
});

it("returns to a safe report deep link after login", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter
      initialEntries={[
        "/login?returnTo=%2Freports%3FreportId%3DR-POST-20260823",
      ]}
    >
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/reports" element={<h1>目标报告</h1>} />
      </Routes>
    </MemoryRouter>,
  );

  await user.type(screen.getByLabelText("邮箱"), "user@example.com");
  await user.type(screen.getByLabelText("登录密码"), "password");
  await user.click(screen.getByRole("button", { name: "登录工作台" }));

  expect(authMocks.login).toHaveBeenCalledWith("user@example.com", "password");
  expect(
    await screen.findByRole("heading", { name: "目标报告" }),
  ).toBeInTheDocument();
});

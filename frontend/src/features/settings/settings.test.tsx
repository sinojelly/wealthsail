import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPage from "./SettingsPage";
import { renderWithSession } from "../../test/render-with-session";
import { meFixture } from "../../test/fixtures";

vi.mock("../../api/hooks", () => ({
  useMe: () => ({ data: meFixture, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useCreateBindingCredential: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockResolvedValue({
      token: "WS-TEST-1234",
      expiresInSeconds: 900,
      channelType: "wecom",
    }),
    isPending: false,
    isError: false,
    error: null,
  }),
  useConsumeBindingCredential: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockResolvedValue({
      bindingId: "binding-001",
      status: "ACTIVE",
      channelType: "wecom",
    }),
    isPending: false,
    isError: false,
    error: null,
  }),
}));

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

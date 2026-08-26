import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPage from "./SettingsPage";
import WeComBindingCard from "./components/WeComBindingCard";
import { renderWithSession } from "../../test/render-with-session";
import { meFixture } from "../../test/fixtures";

const { revokeBinding } = vi.hoisted(() => ({
  revokeBinding: vi.fn().mockResolvedValue({ bindingId: "binding-001", status: "REVOKED" }),
}));

vi.mock("../../api/hooks", () => ({
  useMe: () => ({ data: meFixture, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  useCreateBindingCredential: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockResolvedValue({
      token: "WS-TEST-1234-SECURE",
      expiresInSeconds: 900,
      channelType: "wecom",
    }),
    isPending: false,
    isError: false,
    error: null,
  }),
  useRevokeBinding: () => ({
    mutate: vi.fn(),
    mutateAsync: revokeBinding,
    isPending: false,
    isError: false,
    error: null,
  }),
}));

it("waits for a verified WeCom callback instead of simulating an external identity", async () => {
  const user = userEvent.setup();
  renderWithSession(<SettingsPage />);

  await user.click(screen.getByRole("button", { name: "生成一次性绑定凭证" }));
  expect(await screen.findByText("等待企业微信回调确认")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /模拟企业微信确认/ })).not.toBeInTheDocument();
  expect(screen.getByText("/bind WS-TEST-1234-SECURE")).toBeInTheDocument();
});

it("revokes an active binding only through the reauthenticated backend command", async () => {
  const user = userEvent.setup();
  renderWithSession(
    <WeComBindingCard
      bindings={[{
        id: "binding-001",
        channelType: "wecom",
        externalUsername: "试点员工",
        status: "ACTIVE",
        boundAt: "2026-08-26T09:00:00+08:00",
        lastUsedAt: null,
      }]}
    />,
  );

  await user.click(screen.getByRole("button", { name: "解除绑定" }));
  await user.type(screen.getByLabelText("解除绑定重新验证口令"), "owner-passphrase");
  await user.click(screen.getByRole("button", { name: "确认解除绑定" }));

  expect(revokeBinding).toHaveBeenCalledWith({
    bindingId: "binding-001",
    password: "owner-passphrase",
  });
  expect(await screen.findByText("已撤销")).toBeInTheDocument();
});

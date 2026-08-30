import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPage from "./SettingsPage";
import WeComBindingCard from "./components/WeComBindingCard";
import { renderWithSession } from "../../test/render-with-session";
import { meFixture } from "../../test/fixtures";

const {
  createInvitation,
  createPersonalTenantInvitation,
  revokeBinding,
  updateTenantName,
} = vi.hoisted(() => ({
  createInvitation: vi.fn().mockResolvedValue({
    invitationId: "invite-001",
    token: "INVITE-OWNER-ONLY-TEST-TOKEN",
    email: "new.admin@example.com",
    role: "ADMIN",
    expiresAt: "2026-09-02T09:30:00+08:00",
  }),
  createPersonalTenantInvitation: vi.fn().mockResolvedValue({
    invitationId: "invite-personal-001",
    token: "INVITE-PERSONAL-TENANT-TEST-TOKEN",
    email: "personal@example.com",
    tenantName: "个人投资账户",
    purpose: "CREATE_PERSONAL_TENANT",
    role: "OWNER",
    expiresAt: "2026-09-06T09:30:00+08:00",
  }),
  revokeBinding: vi
    .fn()
    .mockResolvedValue({ bindingId: "binding-001", status: "REVOKED" }),
  updateTenantName: vi.fn().mockResolvedValue({
    tenantId: "tenant-demo-001",
    name: "Jelly家庭投资账户",
  }),
}));

vi.mock("../../api/hooks", () => ({
  useMe: () => ({
    data: meFixture,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateBindingCredential: () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn().mockResolvedValue({
      token: "WS-TEST12",
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
  useCreateInvitation: () => ({
    mutateAsync: createInvitation,
    isPending: false,
    error: null,
  }),
  useCreatePersonalTenantInvitation: () => ({
    mutateAsync: createPersonalTenantInvitation,
    isPending: false,
    error: null,
  }),
  useUpdateTenantName: () => ({
    mutateAsync: updateTenantName,
    isPending: false,
    error: null,
  }),
}));

afterEach(() => {
  meFixture.activeRole = "OWNER";
  meFixture.platformRoles = [];
  vi.clearAllMocks();
});

it("lets an owner create a one-time administrator invitation", async () => {
  const user = userEvent.setup();
  renderWithSession(<SettingsPage />);

  await user.type(screen.getByLabelText("受邀邮箱"), "new.admin@example.com");
  await user.selectOptions(screen.getByLabelText("成员角色"), "ADMIN");
  await user.click(screen.getByRole("button", { name: "生成邀请码" }));

  expect(createInvitation).toHaveBeenCalledWith({
    email: "new.admin@example.com",
    role: "ADMIN",
  });
  expect(
    await screen.findByText("INVITE-OWNER-ONLY-TEST-TOKEN"),
  ).toBeInTheDocument();
  expect(screen.getByText("邀请码只显示一次")).toBeInTheDocument();
});

it("does not render invitation controls for a viewer", () => {
  meFixture.activeRole = "VIEWER";
  renderWithSession(<SettingsPage />);

  expect(
    screen.queryByRole("heading", { name: "邀请成员" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "生成邀请码" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "保存租户名称" }),
  ).not.toBeInTheDocument();
});

it("lets the active tenant owner rename the tenant", async () => {
  const user = userEvent.setup();
  renderWithSession(<SettingsPage />);

  const input = screen.getByLabelText("租户名称");
  const saveButton = screen.getByRole("button", { name: "保存租户名称" });
  const controls = input.closest(".tenant-name-controls");
  expect(controls).not.toBeNull();
  expect(controls).toContainElement(saveButton);
  expect(input).toHaveValue("明远家庭账户");
  await user.clear(input);
  await user.type(input, "Jelly家庭投资账户");
  await user.click(saveButton);

  expect(updateTenantName).toHaveBeenCalledWith({ name: "Jelly家庭投资账户" });
  expect(await screen.findByText("租户名称已更新")).toBeInTheDocument();
});

it("lets a platform administrator create an independent personal-tenant invitation", async () => {
  meFixture.platformRoles = ["PLATFORM_ADMIN"];
  const user = userEvent.setup();
  renderWithSession(<SettingsPage />);

  await user.type(
    screen.getByLabelText("独立租户受邀邮箱"),
    "personal@example.com",
  );
  await user.type(screen.getByLabelText("个人租户名称"), "个人投资账户");
  await user.click(screen.getByRole("button", { name: "创建独立租户邀请码" }));

  expect(createPersonalTenantInvitation).toHaveBeenCalledWith({
    email: "personal@example.com",
    tenantName: "个人投资账户",
  });
  expect(
    await screen.findByText("INVITE-PERSONAL-TENANT-TEST-TOKEN"),
  ).toBeInTheDocument();
});

it("does not expose platform governance controls to a tenant owner", () => {
  renderWithSession(<SettingsPage />);

  expect(
    screen.queryByRole("heading", { name: "创建独立租户" }),
  ).not.toBeInTheDocument();
});

it("waits for a verified WeCom callback instead of simulating an external identity", async () => {
  const user = userEvent.setup();
  renderWithSession(<SettingsPage />);

  await user.click(screen.getByRole("button", { name: "生成一次性绑定凭证" }));
  expect(await screen.findByText("等待企业微信回调确认")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /模拟企业微信确认/ }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("/bind WS-TEST12")).toBeInTheDocument();
});

it("revokes an active binding only through the reauthenticated backend command", async () => {
  const user = userEvent.setup();
  renderWithSession(
    <WeComBindingCard
      bindings={[
        {
          id: "binding-001",
          channelType: "wecom",
          externalUsername: "试点员工",
          status: "ACTIVE",
          boundAt: "2026-08-26T09:00:00+08:00",
          lastUsedAt: null,
        },
      ]}
    />,
  );

  await user.click(screen.getByRole("button", { name: "解除绑定" }));
  await user.type(
    screen.getByLabelText("解除绑定重新验证口令"),
    "owner-passphrase",
  );
  await user.click(screen.getByRole("button", { name: "确认解除绑定" }));

  expect(revokeBinding).toHaveBeenCalledWith({
    bindingId: "binding-001",
    password: "owner-passphrase",
  });
  expect(await screen.findByText("已撤销")).toBeInTheDocument();
});

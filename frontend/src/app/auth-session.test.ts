import { queryClient } from "./query-client";
import {
  getSessionSubject,
  logout,
  privateQueryKey,
  type AuthSession,
} from "./auth-session";

const session: AuthSession = {
  userId: "user-a",
  displayName: "A",
  email: "a@example.test",
  tenantId: "tenant-a",
  tenantName: "A tenant",
  role: "OWNER",
  accessToken: "token-a",
  expiresIn: 3600,
};

it("builds every private query key from API version and active subject", () => {
  expect(getSessionSubject(session)).toBe("tenant-a:user-a");
  expect(privateQueryKey(session, "portfolio", "summary")).toEqual([
    "v1",
    "tenant-a:user-a",
    "portfolio",
    "summary",
  ]);
});

it("logout removes private query data before another user can render it", async () => {
  queryClient.setQueryData(
    ["v1", "tenant-a:user-a", "portfolio", "summary"],
    { totalAsset: "100.00" },
  );

  await logout();

  expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
});

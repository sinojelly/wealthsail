import { afterEach, expect, it, vi } from "vitest";
import { request, setAccessToken } from "./client";

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
});

it("adds the installed access token to authenticated API requests", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        data: { id: "user-1" },
        meta: { requestId: "req-1", asOf: "2026-08-28T00:00:00+08:00" },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  setAccessToken("signed-access-token");

  await request("/v1/auth/me");

  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(new Headers(init.headers).get("Authorization")).toBe(
    "Bearer signed-access-token",
  );
  expect(init.credentials).toBe("include");
});

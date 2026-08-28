import assert from "node:assert/strict";
import test from "node:test";

import {
  assertSafeSmokeRequest,
  isExpectedAnonymousRefreshFailure,
  loadUiSmokeConfig,
} from "./ui-smoke-config.mjs";

test("requires credentials from the execution environment", () => {
  assert.throws(
    () => loadUiSmokeConfig({ WS_SMOKE_BASE_URL: "https://test.example" }),
    /WS_SMOKE_EMAIL.*WS_SMOKE_PASSWORD/,
  );
});

test("rejects plaintext HTTP for non-loopback targets", () => {
  assert.throws(
    () =>
      loadUiSmokeConfig({
        WS_SMOKE_BASE_URL: "http://wealthsail.example",
        WS_SMOKE_EMAIL: "smoke@example.test",
        WS_SMOKE_PASSWORD: "test-only-password",
      }),
    /HTTPS/,
  );
});

test("defaults to a read-only smoke journey", () => {
  const config = loadUiSmokeConfig({
    WS_SMOKE_BASE_URL: "https://wealthsail.example",
    WS_SMOKE_EMAIL: "smoke@example.test",
    WS_SMOKE_PASSWORD: "test-only-password",
  });

  assert.equal(config.baseUrl, "https://wealthsail.example");
  assert.equal(config.readOnly, true);
});

test("allows only authentication lifecycle writes", () => {
  const baseUrl = "http://127.0.0.1:5173";
  for (const path of ["login", "refresh", "logout"]) {
    assert.doesNotThrow(() =>
      assertSafeSmokeRequest(
        `${baseUrl}/api/v1/auth/${path}`,
        "POST",
        baseUrl,
      ),
    );
  }
  assert.doesNotThrow(() =>
    assertSafeSmokeRequest(`${baseUrl}/api/v1/portfolio/summary`, "GET", baseUrl),
  );
});

test("blocks business writes and cross-origin API requests", () => {
  const baseUrl = "http://127.0.0.1:5173";
  assert.throws(
    () =>
      assertSafeSmokeRequest(
        `${baseUrl}/api/v1/reports/generate`,
        "POST",
        baseUrl,
      ),
    /unsafe write/i,
  );
  assert.throws(
    () =>
      assertSafeSmokeRequest(
        "https://evil.example/api/v1/portfolio/summary",
        "GET",
        baseUrl,
      ),
    /cross-origin/i,
  );
});

test("allows only the initial anonymous refresh to return 401", () => {
  assert.equal(
    isExpectedAnonymousRefreshFailure(
      "/api/v1/auth/refresh",
      401,
      false,
    ),
    true,
  );
  assert.equal(
    isExpectedAnonymousRefreshFailure(
      "/api/v1/auth/refresh",
      401,
      true,
    ),
    false,
  );
  assert.equal(
    isExpectedAnonymousRefreshFailure(
      "/api/v1/portfolio/summary",
      503,
      false,
    ),
    false,
  );
});

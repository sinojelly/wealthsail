import assert from "node:assert/strict";
import test from "node:test";

import { loadUiSmokeConfig } from "./ui-smoke-config.mjs";

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

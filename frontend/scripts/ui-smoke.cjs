/* WealthSail read-only business smoke: login -> dashboard -> account -> refresh -> logout. */
const { chromium } = require("playwright");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

(async () => {
  const {
    assertSafeSmokeRequest,
    isExpectedAnonymousRefreshFailure,
    loadUiSmokeConfig,
  } = await import(
    "./ui-smoke-config.mjs"
  );
  const config = loadUiSmokeConfig();
  if (!config.readOnly) throw new Error("UI smoke must remain read-only");

  const artifactDir = process.env.WS_SMOKE_ARTIFACT_DIR || os.tmpdir();
  fs.mkdirSync(artifactDir, { recursive: true });
  const screenshotPath = path.join(artifactDir, "wealthsail-ui-smoke.png");

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const runtimeErrors = [];
  const successfulAuthWrites = new Set();

  page.on("pageerror", (error) => runtimeErrors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (
      message.type() === "error" &&
      !message.text().includes("favicon") &&
      !message.text().startsWith("Failed to load resource:")
    ) {
      runtimeErrors.push(`console: ${message.text()}`);
    }
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    const hasLoggedIn = successfulAuthWrites.has("/api/v1/auth/login");
    if (
      url.origin === new URL(config.baseUrl).origin &&
      url.pathname.startsWith("/api/") &&
      response.status() >= 400 &&
      !isExpectedAnonymousRefreshFailure(
        url.pathname,
        response.status(),
        hasLoggedIn,
      )
    ) {
      runtimeErrors.push(`http: ${response.status()} ${url.pathname}`);
    }
    if (
      response.request().method() === "POST" &&
      url.pathname.startsWith("/api/v1/auth/") &&
      response.ok()
    ) {
      successfulAuthWrites.add(url.pathname);
    }
  });
  await page.route("**/*", async (route) => {
    const request = route.request();
    try {
      assertSafeSmokeRequest(request.url(), request.method(), config.baseUrl);
      await route.continue();
    } catch (error) {
      runtimeErrors.push(error.message);
      await route.abort("blockedbyclient");
    }
  });

  const step = (name) => console.log(`OK  ${name}`);
  try {
    await page.goto(`${config.baseUrl}/login`, {
      waitUntil: "domcontentloaded",
      timeout: 45_000,
    });
    await page.getByLabel("邮箱").waitFor({ timeout: 15_000 });
    step("login page rendered");

    await page.getByLabel("邮箱").fill(config.email);
    await page.getByLabel("登录密码").fill(config.password);
    await page.getByRole("button", { name: "登录工作台" }).click();
    await page.getByRole("heading", { name: "资产看板" }).waitFor({ timeout: 30_000 });
    step("dedicated smoke account logged in and dashboard rendered");

    await page.getByRole("link", { name: "账号安全" }).click();
    await page.getByRole("heading", { name: "账号安全" }).waitFor({ timeout: 15_000 });
    await page.getByText("仅内存 · 不落盘", { exact: true }).waitFor({ timeout: 20_000 });
    step("authenticated account endpoint rendered");

    await page.reload({ waitUntil: "domcontentloaded", timeout: 45_000 });
    await page.getByRole("heading", { name: "账号安全" }).waitFor({ timeout: 30_000 });
    await page.getByText("仅内存 · 不落盘", { exact: true }).waitFor({ timeout: 20_000 });
    if (!successfulAuthWrites.has("/api/v1/auth/refresh")) {
      throw new Error("refresh lifecycle did not complete successfully after page reload");
    }
    step("HttpOnly refresh session restored after reload");

    const logoutResponse = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/v1/auth/logout" && response.ok(),
      { timeout: 15_000 },
    );
    await page.getByRole("button", { name: "退出登录" }).click();
    await logoutResponse;
    await page.getByRole("button", { name: "登录工作台" }).waitFor({ timeout: 15_000 });
    step("logout revoked the refresh session and returned to login");

    for (const requiredPath of ["/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout"]) {
      if (!successfulAuthWrites.has(requiredPath)) {
        throw new Error(`required authentication operation did not succeed: ${requiredPath}`);
      }
    }
    if (runtimeErrors.length > 0) {
      throw new Error(`browser runtime errors: ${runtimeErrors.join(" | ")}`);
    }
    await page.screenshot({ path: screenshotPath, fullPage: true });
    console.log(`ARTIFACT ${screenshotPath}`);
  } catch (error) {
    await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(`FAILED: ${error.message}`);
  process.exit(1);
});

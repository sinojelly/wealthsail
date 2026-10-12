/* Local-only acceptance: real API and fixture PostgreSQL, no real trading account. */
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const base = process.env.INVESTMENT_SMOKE_BASE_URL || "http://127.0.0.1:5173";
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname))
  throw new Error("investment smoke requires an isolated loopback target");
const artifacts = path.resolve(
  __dirname,
  "../../artifacts/investment-opportunities",
);
fs.mkdirSync(artifacts, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width: 1600, height: 1050 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("response", (r) => {
    if (
      r.url().includes("/api/") &&
      r.status() >= 400 &&
      !r.url().includes("/auth/refresh")
    )
      errors.push(`${r.status()} ${new URL(r.url()).pathname}`);
  });
  await page.route("https://fonts.googleapis.com/**", (r) =>
    r.fulfill({ status: 200, contentType: "text/css", body: "" }),
  );
  try {
    await page.goto(`${base}/login`);
    await page
      .getByLabel("邮箱")
      .fill(
        process.env.INVESTMENT_SMOKE_EMAIL || "ci-smoke@wealthsail.example",
      );
    await page
      .getByLabel("登录密码")
      .fill(process.env.INVESTMENT_SMOKE_PASSWORD || "SmokeLogin-Only-2026!");
    await page.getByRole("button", { name: "登录工作台" }).click();
    await page
      .getByRole("heading", { name: "资产看板", exact: true })
      .waitFor();
    await page.getByRole("link", { name: "投资机会", exact: true }).click();
    await page
      .getByRole("heading", { name: "投资机会中心", exact: true })
      .waitFor();
    await page.getByRole("button", { name: /沪深300 当前指数/ }).waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "关注指数", exact: true })
        .isEnabled(),
      false,
    );
    await page.getByRole("button", { name: /华夏沪深300ETF/ }).click();
    await page.getByLabel("研究路径").filter({ hasText: "510330" }).waitFor();
    assert.match(
      await page.getByLabel("研究路径").innerText(),
      /A股 \/ 宽基 \/ 沪深300 \/ 510330/,
    );
    assert.equal(
      await page
        .getByRole("button", { name: /沪深300 当前指数/ })
        .getAttribute("aria-pressed"),
      "true",
    );
    const watch = page.getByRole("button", { name: "加入自选基金", exact: true });
    if(await watch.isEnabled()) await watch.click();
    await page.getByText("已加入自选", { exact: true }).waitFor();
    await page.screenshot({
      path: path.join(artifacts, "desktop.png"),
      fullPage: true,
    });
    await page.getByRole("tab", { name: "技术趋势" }).click();
    await page
      .getByText("暂无可靠历史序列，无法绘制趋势图。", { exact: true })
      .waitFor();
    await page.getByRole("combobox", { name: "全局搜索" }).fill("恒生");
    await page.getByRole("option", { name: /恒生指数/ }).click();
    await page.getByLabel("研究路径").filter({ hasText: "恒生指数" }).waitFor();
    assert.match(
      await page.getByLabel("研究路径").innerText(),
      /港股 \/ 宽基 \/ 恒生指数/,
    );
    await page.getByRole("button", { name: "A股", exact: true }).click();
    await page.getByRole("button", { name: /沪深300 当前指数/ }).waitFor();
    await page.reload();
    await page
      .getByRole("heading", { name: "投资机会中心", exact: true })
      .waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: /华夏沪深300ETF/ }).click();
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
      true,
      "mobile must not overflow the document",
    );
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByRole("combobox", { name: "全局搜索" }).waitFor();
    await page.screenshot({
      path: path.join(artifacts, "mobile.png"),
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    console.log(
      "PASS: real login/API, parent selection, global market location, watchlist, URL reload, honest empty trend, desktop/mobile; Browser plugin not available — Playwright used.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});

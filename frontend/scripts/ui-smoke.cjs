/* WealthSail UI 只读冒烟：登录 → 看板 → 打开持仓弹窗 → 搜索标的。 */
const { chromium } = require("playwright");
const os = require("node:os");
const path = require("node:path");

(async () => {
  const { loadUiSmokeConfig } = await import("./ui-smoke-config.mjs");
  const config = loadUiSmokeConfig();
  if (!config.readOnly) throw new Error("UI smoke must remain read-only");
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push("console: " + m.text());
  });

  const step = (name) => console.log("✔ " + name);

  await page.goto(config.baseUrl + "/", { waitUntil: "networkidle", timeout: 45000 });
  await page.waitForSelector('input[type="email"]', { timeout: 15000 });
  step("首页加载，登录表单可见");

  await page.fill('input[type="email"]', config.email);
  await page.fill('input[type="password"]', config.password);
  await page.click('button[type="submit"], button.btn-primary');
  await page.waitForSelector("text=资产看板", { timeout: 20000 });
  step("登录成功，进入资产看板");

  const addBtn = page.locator('button:has-text("初始化持仓")').first();
  await addBtn.waitFor({ timeout: 10000 });
  step("「初始化持仓」按钮存在");

  await addBtn.click();
  await page.waitForSelector('role=dialog >> text=初始化一笔持仓', { timeout: 8000 });
  step("弹窗打开（期初组合初始化）");

  await page.fill('#position-search', "600519");
  await page.waitForSelector(".search-result-row", { timeout: 15000 });
  const rowText = await page.locator(".search-result-row").first().innerText();
  console.log("    搜索命中: " + rowText.replace(/\n/g, " "));
  await page.locator(".search-result-row").first().click();

  step("只读搜索完成；未提交任何持仓或其他经济事实");

  const screenshotPath = path.join(os.tmpdir(), "wealthsail-ui-smoke.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  console.log("    截图: " + screenshotPath);

  const jsErrors = errors.filter((e) => !e.includes("favicon"));
  console.log(jsErrors.length === 0 ? "✔ 无页面 JS 错误" : "✘ 页面错误: " + jsErrors.join(" | "));
  await browser.close();
})().catch((e) => {
  console.error("FAILED:", e.message);
  process.exit(1);
});

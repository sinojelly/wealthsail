/* WealthSail 前端 UI 冒烟：登录 → 看板 → 添加持仓弹窗 → 搜索选标的 → 提交 → 列表刷新 */
const { chromium } = require("playwright");

const BASE = "http://122.51.52.38";

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push("console: " + m.text());
  });

  const step = (name) => console.log("✔ " + name);

  await page.goto(BASE + "/", { waitUntil: "networkidle", timeout: 45000 });
  await page.waitForSelector('input[type="email"]', { timeout: 15000 });
  step("首页加载，登录表单可见");

  await page.fill('input[type="email"]', "3452734723@qq.com");
  await page.fill('input[type="password"]', "zdt012123");
  await page.click('button[type="submit"], button.btn-primary');
  await page.waitForSelector("text=资产看板", { timeout: 20000 });
  step("登录成功，进入资产看板");

  const addBtn = page.locator('button:has-text("添加持仓")').first();
  await addBtn.waitFor({ timeout: 10000 });
  step("「+ 添加持仓」按钮存在");

  await addBtn.click();
  await page.waitForSelector('role=dialog >> text=添加持仓', { timeout: 8000 });
  step("弹窗打开（添加持仓）");

  await page.fill('#position-search', "600519");
  await page.waitForSelector(".search-result-row", { timeout: 15000 });
  const rowText = await page.locator(".search-result-row").first().innerText();
  console.log("    搜索命中: " + rowText.replace(/\n/g, " "));
  await page.locator(".search-result-row").first().click();

  await page.fill("#position-quantity", "50");
  await page.fill("#position-avg-cost", "1350.00");
  await page.click('button:has-text("确认添加")');
  await page.waitForSelector('role=dialog >> text=添加持仓', { state: "detached", timeout: 20000 });
  step("提交成功，弹窗关闭");

  await page.waitForSelector('text=贵州茅台', { timeout: 15000 });
  const row = page.locator("tbody tr", { hasText: "600519" }).first();
  const cells = await row.innerText();
  console.log("    持仓行: " + cells.replace(/\t/g, " | ").replace(/\n/g, " "));

  await page.screenshot({ path: "/tmp/ws_dashboard.png", fullPage: true });
  console.log("    截图: /tmp/ws_dashboard.png");

  const jsErrors = errors.filter((e) => !e.includes("favicon"));
  console.log(jsErrors.length === 0 ? "✔ 无页面 JS 错误" : "✘ 页面错误: " + jsErrors.join(" | "));
  await browser.close();
})().catch((e) => {
  console.error("FAILED:", e.message);
  process.exit(1);
});

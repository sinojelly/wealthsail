// WealthSail 前端端到端浏览器验证（playwright chromium headless）。
// 运行：node scripts/e2e-verify.mjs
// 前置：后端 uvicorn :8000 + 前端 vite dev :5173 均在运行。
import { chromium } from "playwright";

const BASE = "http://127.0.0.1:5173";
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  ${detail}` : ""}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (err) => errors.push(String(err)));
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(msg.text());
});

try {
  // 1. 未登录 → 重定向登录页
  await page.goto(`${BASE}/dashboard`, { waitUntil: "networkidle" });
  check("未登录重定向到 /login", page.url().includes("/login"), page.url());
  check("登录页渲染", await page.getByText("登录工作台").isVisible());

  // 2. 快速登录 张明
  await page.getByRole("button", { name: /张明/ }).click();
  await page.waitForURL("**/dashboard", { timeout: 15000 });
  check("登录后跳转资产看板", page.url().includes("/dashboard"));

  // 3. 资产看板：真实后端数据
  await page.getByText("贵州茅台").waitFor({ timeout: 15000 });
  check("持仓表渲染 贵州茅台", true);
  const body = await page.locator("body").innerText();
  check("总资产 506,000.00", body.includes("506,000.00"));
  check("当日收益 +6,000.00", body.includes("+¥6,000.00"));
  check("资产配置 A股", body.includes("A股"));
  await page.screenshot({ path: "/tmp/e2e-dashboard.png", fullPage: true });
  console.log("screenshot: /tmp/e2e-dashboard.png");

  // 4. 研报审计：四时段 + 详情 + 版本链
  await page.getByRole("link", { name: "研报审计" }).click();
  await page.getByText("盘前简报").waitFor({ timeout: 15000 });
  check("四时段报告列表渲染", true);
  // 盘前简报存在 v2 修订（supersedes v1），可验证版本审计链
  await page.getByRole("button", { name: /盘前简报/ }).click();
  await page.locator(".report-viewer").getByText(/修订版/).waitFor({ timeout: 15000 });
  check("报告详情（摘要）渲染", true);
  const rbody = await page.locator(".report-viewer").innerText();
  check("版本审计链展示", rbody.includes("版本审计链"));
  check("版本链含 v1/v2", rbody.includes("v1") && rbody.includes("v2"));
  await page.screenshot({ path: "/tmp/e2e-reports.png", fullPage: true });

  // 5. 风控政策：当前政策 + 合规检查
  await page.getByRole("link", { name: "风控政策" }).click();
  await page.getByText("当前生效政策").waitFor({ timeout: 15000 });
  const rk = await page.locator("body").innerText();
  check("政策版本 v3", rk.includes("v3"));
  check("合规检查渲染", rk.includes("单一标的"));
  await page.screenshot({ path: "/tmp/e2e-risk.png", fullPage: true });

  // 6. 账号安全：真实用户会话 + 绑定流程
  await page.getByRole("link", { name: "账号安全" }).click();
  await page.getByText("张明", { exact: true }).waitFor({ timeout: 15000 });
  const st = await page.locator("body").innerText();
  check("会话显示真实用户 张明", st.includes("张明"));
  check("租户与角色渲染", st.includes("OWNER"));
  await page.screenshot({ path: "/tmp/e2e-settings.png", fullPage: true });

  // 7. 运维监控：作业运行 + 审计
  await page.getByRole("link", { name: "运维监控" }).click();
  await page.getByText("intraday_tracking", { exact: false }).first().waitFor({ timeout: 15000 });
  check("作业运行表渲染", true);
  await page.getByRole("tab", { name: "审计日志" }).click();
  await page.getByText("REPORT_GENERATED", { exact: false }).first().waitFor({ timeout: 15000 });
  check("审计日志渲染", true);
  await page.screenshot({ path: "/tmp/e2e-operations.png", fullPage: true });

  // 8. 退出登录
  await page.getByRole("button", { name: "退出登录" }).click();
  await page.waitForURL("**/login");
  check("退出登录回到登录页", page.url().includes("/login"));

  // 9. 直接访问受保护路由 → 被守卫拦截
  await page.goto(`${BASE}/dashboard`, { waitUntil: "networkidle" });
  check("登出后路由守卫拦截", page.url().includes("/login"));
} catch (err) {
  console.error("E2E 失败：", err.message);
  await page.screenshot({ path: "/tmp/e2e-failure.png", fullPage: true });
  results.push({ name: `异常中断: ${err.message}`, ok: false });
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n=== ${results.length - failed.length}/${results.length} 通过 ===`);
if (errors.length) console.log("浏览器控制台错误：", errors.slice(0, 5));
process.exit(failed.length ? 1 : 0);

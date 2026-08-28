// WealthSail 本机白屏问题验证：登录 → 看板数据 → console/network 错误收集
import { chromium } from "playwright";

const BASE = "http://127.0.0.1:5173";
const TEST_EMAIL = process.env.WEALTHSAIL_TEST_EMAIL;
const TEST_PASSWORD = process.env.WEALTHSAIL_TEST_PASSWORD;
if (!TEST_EMAIL || !TEST_PASSWORD) {
  throw new Error(
    "Set WEALTHSAIL_TEST_EMAIL and WEALTHSAIL_TEST_PASSWORD before running this verification.",
  );
}
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  ${detail}` : ""}`);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
const failedReqs = [];
page.on("pageerror", (err) => errors.push("pageerror: " + err.message));
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push("console: " + msg.text());
});
page.on("requestfailed", (req) =>
  failedReqs.push(`${req.method()} ${req.url()} :: ${req.failure()?.errorText ?? "?"}`),
);
page.on("response", (res) => {
  if (res.status() >= 400) failedReqs.push(`HTTP ${res.status()} ${res.url()}`);
});

try {
  // 1. 访问首页（未登录）
  await page.goto(BASE + "/", { waitUntil: "networkidle", timeout: 30000 });
  check("首页加载（无 JS 崩溃白屏）", (await page.locator("#root").innerText()).length > 0);
  check("未登录重定向到 /login", page.url().includes("/login"), page.url());
  check("登录页渲染", await page.getByRole("button", { name: "登录工作台" }).isVisible());

  // 2. 真实登录
  await page.fill('input[type="email"]', TEST_EMAIL);
  await page.fill('input[type="password"]', TEST_PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForURL("**/dashboard", { timeout: 15000 });
  check("登录成功 → 跳转 /dashboard", true);

  // 3. 等真实数据渲染
  await page.getByText("资产看板").waitFor({ timeout: 15000 });
  check("资产看板标题渲染", true);

  // 4. 等 API 数据（React Query 一轮往返）
  await page.waitForTimeout(4000);
  const body = await page.locator("body").innerText();
  check("看板已加载后端数据", body.includes("总资产暂不发布") || body.includes("持仓"),
    body.slice(0, 120).replace(/\n/g, " "));

  await page.screenshot({ path: "/tmp/ws_local_dashboard.png", fullPage: true });
  console.log("    截图: /tmp/ws_local_dashboard.png");

  // 5. 汇总错误
  const filtered = errors.filter((e) => !e.includes("favicon") && !e.includes("401"));
  console.log(filtered.length === 0 ? "✔ 无 JS console/page 错误" : "✘ 页面错误:\n  " + filtered.join("\n  "));
  const realFailures = failedReqs.filter(
    (r) => !r.includes("401") && !r.includes("favicon") && !r.includes("socket.io"),
  );
  console.log(realFailures.length === 0 ? "✔ 无网络请求失败" : "✘ 网络失败:\n  " + realFailures.join("\n  "));
} catch (e) {
  console.error("FAILED:", e.message);
  console.error("收集到的错误:", errors.join("\n  ") || "无");
  console.error("失败请求:", failedReqs.join("\n  ") || "无");
  process.exitCode = 1;
} finally {
  await browser.close();
}

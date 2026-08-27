import { createApiRequester, waitForQueryAccessToken } from "./browser-query.mjs";

const QUERY_URL = "https://query.1234567.com.cn/";

export async function openAuthenticatedSession({
  chromium,
  profileDir,
  browser,
  prompt,
  logger,
}) {
  const launchOptions = {
    headless: false,
    viewport: null,
    acceptDownloads: false,
    args: ["--disable-sync"],
  };
  if (browser !== "chromium") {
    launchOptions.channel = browser;
  }

  const context = await chromium.launchPersistentContext(profileDir, launchOptions);
  try {
    const page = context.pages()[0] || (await context.newPage());
    await page.goto(QUERY_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.bringToFront();

    logger("");
    logger("浏览器已经打开。请在浏览器中完成天天基金登录或扫码确认。");
    logger("看到“交易查询”页面后，回到这个终端继续。采集期间不要关闭浏览器。");
    await prompt("登录完成并看到交易查询页面后，请按 Enter 开始自动采集：");

    // 登录完成后重新导航，让页面按官方流程发出带临时令牌的查询请求。
    // 令牌只保存在当前进程内存中，不写入日志、配置或导出文件。
    const tokenPromise = waitForQueryAccessToken(page, { timeoutMs: 30_000 });
    await page.goto(QUERY_URL, { waitUntil: "domcontentloaded", timeout: 30_000 });
    const accessToken = await tokenPromise;

    return {
      requestPage: createApiRequester({ apiContext: context.request, accessToken }),
      close: () => context.close(),
    };
  } catch (error) {
    await context.close();
    throw error;
  }
}

import { mkdir } from "node:fs/promises";
import process from "node:process";
import { createInterface } from "node:readline/promises";

import { openAuthenticatedSession } from "./browser-session.mjs";
import { parseCliArgs } from "./options.mjs";
import { buildQueryPlans } from "./query.mjs";
import { runCapture } from "./run.mjs";

const HELP = `
天天基金本地交易采集工具

用途：打开独立的可见浏览器，由你扫码登录后，自动采集当前持仓和普通基金成功交易。
工具会生成可上传 WealthSail 的单个 ZIP；账号密码、Cookie 和临时访问令牌不会写入导出文件。

首次推荐：
  pnpm capture -- --from 2020-01-01

你只需要：
  1. 在自动打开的浏览器中扫码登录天天基金；
  2. 看到“交易查询”页面后回到终端按 Enter；
  3. 等待自动采集结束，不要关闭该浏览器。

选项：
  --from YYYY-MM-DD      最早交易日期，默认 2010-01-01
  --to YYYY-MM-DD        截止日期，默认今天
  --browser NAME         msedge（Windows 默认）、chrome 或 chromium
  --delay-ms NUMBER      请求间隔，默认 800，范围 300~10000
  --output-dir PATH      导出目录，默认 private-data/exports
  --profile-dir PATH     独立浏览器资料目录，默认 private-data/browser-profile
  --help                 显示帮助
`;

async function main() {
  const options = parseCliArgs(process.argv.slice(2));
  if (options.help) {
    process.stdout.write(HELP);
    return;
  }

  // 日期和查询计划在打开浏览器前校验，避免用户登录后才发现参数错误。
  const plans = buildQueryPlans({ from: options.from, to: options.to, today: options.today });
  await mkdir(options.outputDir, { recursive: true });
  await mkdir(options.profileDir, { recursive: true });

  const readline = createInterface({ input: process.stdin, output: process.stdout });
  let session;
  try {
    const { chromium } = await import("playwright");
    session = await openAuthenticatedSession({
      chromium,
      profileDir: options.profileDir,
      browser: options.browser,
      prompt: (message) => readline.question(message),
      logger: (message) => process.stdout.write(`${message}\n`),
    });

    process.stdout.write(`\n开始采集当前持仓，以及 ${options.from} 至 ${options.to} 的普通基金成功交易。\n`);
    const delay = async () => {
      const jittered = Math.round(options.delayMs * (0.8 + Math.random() * 0.4));
      await new Promise((resolve) => setTimeout(resolve, jittered));
    };
    const result = await runCapture({
      options,
      plans,
      session,
      delay,
      onProgress: ({ plan, pageNum, totalPages, pageRecordCount }) => {
        process.stdout.write(
          `[${plan.windowStart}~${plan.windowEnd}] ${plan.businessTypeLabel} ` +
            `第 ${pageNum}/${totalPages} 页，本页 ${pageRecordCount} 条\n`,
        );
      },
    });

    process.stdout.write("\n采集完成。\n");
    process.stdout.write(`导出目录：${result.runDir}\n`);
    process.stdout.write(`上传 ZIP：${result.zipPath}\n`);
    process.stdout.write(`当前持仓：${result.manifest.counts.currentPositions} 只\n`);
    process.stdout.write(`普通基金交易：${result.manifest.counts.fundTransactions} 条\n`);
    process.stdout.write(
      `排除活期宝：${result.manifest.counts.cashManagementExcluded} 条；` +
        `待人工复核：${result.manifest.counts.reviewRequired} 条；` +
        `本地去重：${result.localManifest.counts.duplicatesRemoved} 条\n`,
    );
  } finally {
    await session?.close();
    readline.close();
  }
}

main().catch((error) => {
  const hint = /browserType\.launch|Executable doesn't exist|channel/.test(String(error))
    ? "。如果系统没有该浏览器，请改用 --browser chrome，或安装 Playwright Chromium"
    : "";
  process.stderr.write(`采集失败：${error.message}${hint}\n`);
  if (process.env.WEALTHSAIL_IMPORTER_DEBUG === "1") {
    process.stderr.write(`${error.stack}\n`);
  }
  process.exitCode = 1;
});

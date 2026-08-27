import assert from "node:assert/strict";
import test from "node:test";

import { parseCliArgs } from "../src/options.mjs";

test("命令行默认采集至今天并使用独立浏览器资料目录", () => {
  const options = parseCliArgs([], { today: "2026-08-27", platform: "win32" });

  assert.equal(options.from, "2010-01-01");
  assert.equal(options.to, "2026-08-27");
  assert.equal(options.today, "2026-08-27");
  assert.equal(options.browser, "msedge");
  assert.equal(options.delayMs, 800);
  assert.equal(options.outputDir.endsWith("private-data\\exports"), true);
  assert.equal(options.profileDir.endsWith("private-data\\browser-profile"), true);
});

test("命令行接受起止日期、浏览器和请求间隔", () => {
  const options = parseCliArgs(
    ["--from", "2020-01-01", "--to", "2026-08-27", "--browser", "chrome", "--delay-ms", "1200"],
    { today: "2026-08-27", platform: "win32" },
  );

  assert.equal(options.from, "2020-01-01");
  assert.equal(options.to, "2026-08-27");
  assert.equal(options.browser, "chrome");
  assert.equal(options.delayMs, 1200);
});

test("命令行兼容 pnpm 传入的双横线分隔符", () => {
  const options = parseCliArgs(["--", "--from", "2020-01-01"], {
    today: "2026-08-27",
    platform: "win32",
  });

  assert.equal(options.from, "2020-01-01");
});

test("命令行拒绝未知选项和过快请求", () => {
  assert.throws(
    () => parseCliArgs(["--unknown"], { today: "2026-08-27", platform: "win32" }),
    /未知参数/,
  );
  assert.throws(
    () => parseCliArgs(["--delay-ms", "10"], { today: "2026-08-27", platform: "win32" }),
    /请求间隔必须在 300 到 10000 毫秒之间/,
  );
});

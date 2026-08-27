import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import test from "node:test";

const execFileAsync = promisify(execFile);
const cliPath = fileURLToPath(new URL("../src/cli.mjs", import.meta.url));

test("帮助命令无需打开浏览器即可说明最小操作流程", async () => {
  const result = await execFileAsync(process.execPath, [cliPath, "--help"], {
    encoding: "utf8",
  });

  assert.match(result.stdout, /天天基金本地交易采集工具/);
  assert.match(result.stdout, /扫码登录/);
  assert.match(result.stdout, /当前持仓/);
  assert.match(result.stdout, /ZIP/);
  assert.match(result.stdout, /--from 2020-01-01/);
  assert.equal(result.stderr, "");
});

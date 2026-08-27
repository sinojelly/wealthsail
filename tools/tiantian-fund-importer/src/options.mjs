import path from "node:path";
import { fileURLToPath } from "node:url";

const TOOL_ROOT = fileURLToPath(new URL("../", import.meta.url));
const VALUE_OPTIONS = new Set([
  "--from",
  "--to",
  "--browser",
  "--delay-ms",
  "--output-dir",
  "--profile-dir",
]);

function systemToday() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function parseCliArgs(
  argv,
  { today = systemToday(), platform = process.platform, baseDir = TOOL_ROOT } = {},
) {
  const values = {};
  let help = false;
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--") {
      continue;
    }
    if (argument === "--help" || argument === "-h") {
      help = true;
      continue;
    }
    if (!VALUE_OPTIONS.has(argument)) {
      throw new Error(`未知参数：${argument}`);
    }
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`参数 ${argument} 缺少值`);
    }
    values[argument] = value;
    index += 1;
  }

  const browser = values["--browser"] || (platform === "win32" ? "msedge" : "chrome");
  if (!["msedge", "chrome", "chromium"].includes(browser)) {
    throw new Error("浏览器必须是 msedge、chrome 或 chromium");
  }

  const delayMs = Number(values["--delay-ms"] || 800);
  if (!Number.isInteger(delayMs) || delayMs < 300 || delayMs > 10_000) {
    throw new Error("请求间隔必须在 300 到 10000 毫秒之间");
  }

  return {
    help,
    from: values["--from"] || "2010-01-01",
    to: values["--to"] || today,
    today,
    browser,
    delayMs,
    outputDir: path.resolve(values["--output-dir"] || path.join(baseDir, "private-data", "exports")),
    profileDir: path.resolve(
      values["--profile-dir"] || path.join(baseDir, "private-data", "browser-profile"),
    ),
  };
}

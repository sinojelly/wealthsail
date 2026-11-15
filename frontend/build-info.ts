import { execFileSync } from "node:child_process";

const hash = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;
const label = /^[A-Za-z0-9][A-Za-z0-9._/+~-]{0,95}$/;

function git(...args: string[]) {
  try {
    return execFileSync("git", args, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "";
  }
}

export function readBuildInfo(env = process.env) {
  const supplied = env.BUILD_COMMIT?.trim() ?? "";
  const commit = hash.test(supplied)
    ? supplied
    : git("rev-parse", "--verify", "HEAD");
  if (!hash.test(commit)) return { commit: null, describe: "未知" };
  const description = hash.test(supplied)
    ? (env.BUILD_DESCRIBE?.trim() ?? "")
    : git("describe", "--tags", "--always", "--long", "--dirty");
  return {
    commit,
    describe:
      description !== "unknown" && label.test(description)
        ? description
        : commit.slice(0, 12),
  };
}

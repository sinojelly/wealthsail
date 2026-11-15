import { useEffect, useState } from "react";

interface PublishedBuild {
  commit: string;
  describe: string;
}

const CHECK_DEBOUNCE_MS = 1_000;
const CHECK_TIMEOUT_MS = 5_000;

function publishedBuild(value: unknown): PublishedBuild | null {
  if (typeof value !== "object" || value === null) return null;
  const { commit, describe } = value as Partial<PublishedBuild>;
  if (
    typeof commit !== "string" ||
    !/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(commit)
  )
    return null;
  return {
    commit,
    describe:
      typeof describe === "string" &&
      /^[A-Za-z0-9][A-Za-z0-9._/+~-]{0,95}$/.test(describe)
        ? describe
        : commit.slice(0, 12),
  };
}

export default function BuildVersionStatus({
  monitor = import.meta.env.PROD,
  reload = () => window.location.reload(),
}: {
  monitor?: boolean;
  reload?: () => void;
}) {
  const current = __WEALTHSAIL_BUILD__;
  const [update, setUpdate] = useState<PublishedBuild | null>(null);

  useEffect(() => {
    if (!monitor || !current.commit) return;
    let pending: AbortController | undefined;
    let lastCheck = -Infinity;

    async function check(force = false) {
      const now = Date.now();
      if (
        document.visibilityState === "hidden" ||
        pending ||
        (!force && now - lastCheck < CHECK_DEBOUNCE_MS)
      )
        return;
      lastCheck = now;
      const request = new AbortController();
      pending = request;
      const timeout = setTimeout(() => request.abort(), CHECK_TIMEOUT_MS);
      try {
        const response = await fetch("/build-info.json", {
          cache: "no-store",
          credentials: "omit",
          signal: request.signal,
        });
        if (!response.ok) return;
        const version = publishedBuild(await response.json());
        if (!request.signal.aborted && version)
          setUpdate(version.commit === current.commit ? null : version);
      } catch {
        // An unavailable probe does not change the active page or a known update.
      } finally {
        clearTimeout(timeout);
        pending = undefined;
      }
    }

    const revisit = () => void check();
    const restore = (event: PageTransitionEvent) => {
      if (event.persisted) void check(true);
    };
    window.addEventListener("focus", revisit);
    window.addEventListener("pageshow", restore);
    document.addEventListener("visibilitychange", revisit);
    revisit();
    return () => {
      pending?.abort();
      window.removeEventListener("focus", revisit);
      window.removeEventListener("pageshow", restore);
      document.removeEventListener("visibilitychange", revisit);
    };
  }, [current.commit, monitor]);

  return (
    <div className="topbar-release">
      <span
        className="topbar-build"
        aria-label={`网页版本 ${current.describe}`}
        title={current.commit ?? "此构建缺少 Git 信息"}
      >
        版本 <code>{current.describe}</code>
      </span>
      {update && (
        <div className="topbar-build-update">
          <span role="status">发现新版本 {update.describe}</span>
          <button type="button" onClick={reload}>
            刷新到最新版本
          </button>
        </div>
      )}
    </div>
  );
}

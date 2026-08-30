const RELOAD_COOLDOWN_MS = 60_000;
const RELOAD_GUARD_PREFIX = "wealthsail:stale-build-reload:";

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): unknown;
}

interface StaleBuildRecoveryOptions {
  target: EventTarget;
  storage: StorageLike;
  reload: () => void;
  buildId: string;
  now?: () => number;
}

/**
 * Vite emits `vite:preloadError` when an already-open page requests a lazy
 * chunk that disappeared during a newer deployment. Refresh once so the
 * browser loads the current index and entry bundle; let a repeated failure
 * reach React's error boundary instead of creating an infinite reload loop.
 */
export function installStaleBuildRecovery({
  target,
  storage,
  reload,
  buildId,
  now = Date.now,
}: StaleBuildRecoveryOptions): () => void {
  const storageKey = `${RELOAD_GUARD_PREFIX}${buildId}`;
  const onPreloadError: EventListener = (event) => {
    const currentTime = now();
    let previousReload: number | null;
    try {
      const storedValue = storage.getItem(storageKey);
      const parsedValue =
        storedValue === null ? Number.NaN : Number(storedValue);
      previousReload = Number.isFinite(parsedValue) ? parsedValue : null;
      if (
        previousReload !== null &&
        currentTime - previousReload < RELOAD_COOLDOWN_MS
      ) {
        return;
      }
      storage.setItem(storageKey, String(currentTime));
    } catch {
      // Storage may be disabled. Do not suppress the error: the root boundary
      // must remain able to show an explicit recovery action.
      return;
    }

    event.preventDefault();
    reload();
  };

  target.addEventListener("vite:preloadError", onPreloadError);
  return () => target.removeEventListener("vite:preloadError", onPreloadError);
}

function currentEntryBuildId(documentObject: Document): string {
  return (
    documentObject
      .querySelector<HTMLScriptElement>('script[type="module"][src]')
      ?.getAttribute("src") ?? "unknown-entry"
  );
}

export function installBrowserStaleBuildRecovery(): () => void {
  return installStaleBuildRecovery({
    target: window,
    storage: window.sessionStorage,
    reload: () => window.location.reload(),
    buildId: currentEntryBuildId(document),
  });
}

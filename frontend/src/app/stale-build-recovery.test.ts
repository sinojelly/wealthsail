import { installStaleBuildRecovery } from "./stale-build-recovery";

function createStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
}

it("reloads once when the active frontend build can no longer load a lazy chunk", () => {
  const target = new EventTarget();
  const storage = createStorage();
  const reload = vi.fn();
  installStaleBuildRecovery({
    target,
    storage,
    reload,
    buildId: "/assets/index-old.js",
    now: () => 100_000,
  });

  const event = new Event("vite:preloadError", { cancelable: true });
  target.dispatchEvent(event);

  expect(event.defaultPrevented).toBe(true);
  expect(reload).toHaveBeenCalledTimes(1);
  expect(
    storage.getItem("wealthsail:stale-build-reload:/assets/index-old.js"),
  ).toBe("100000");
});

it("does not enter a reload loop when the same build fails again immediately", () => {
  const target = new EventTarget();
  const storage = createStorage({
    "wealthsail:stale-build-reload:/assets/index-broken.js": "90000",
  });
  const reload = vi.fn();
  installStaleBuildRecovery({
    target,
    storage,
    reload,
    buildId: "/assets/index-broken.js",
    now: () => 100_000,
  });

  const event = new Event("vite:preloadError", { cancelable: true });
  target.dispatchEvent(event);

  expect(event.defaultPrevented).toBe(false);
  expect(reload).not.toHaveBeenCalled();
});

it("permits recovery again after the reload-loop cooldown", () => {
  const target = new EventTarget();
  const storage = createStorage({
    "wealthsail:stale-build-reload:/assets/index-old.js": "10000",
  });
  const reload = vi.fn();
  installStaleBuildRecovery({
    target,
    storage,
    reload,
    buildId: "/assets/index-old.js",
    now: () => 100_000,
  });

  const event = new Event("vite:preloadError", { cancelable: true });
  target.dispatchEvent(event);

  expect(event.defaultPrevented).toBe(true);
  expect(reload).toHaveBeenCalledTimes(1);
});

it("falls through to the React error boundary when session storage is unavailable", () => {
  const target = new EventTarget();
  const reload = vi.fn();
  installStaleBuildRecovery({
    target,
    storage: {
      getItem: () => {
        throw new DOMException("storage disabled");
      },
      setItem: () => undefined,
    },
    reload,
    buildId: "/assets/index-old.js",
    now: () => 100_000,
  });

  const event = new Event("vite:preloadError", { cancelable: true });
  target.dispatchEvent(event);

  expect(event.defaultPrevented).toBe(false);
  expect(reload).not.toHaveBeenCalled();
});

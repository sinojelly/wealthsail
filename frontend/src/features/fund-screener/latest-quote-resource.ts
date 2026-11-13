import { useCallback, useSyncExternalStore } from "react";
import {
  getLatestFundQuotes,
  refreshFundQuotes,
  type LatestFundQuote,
} from "../../api/funds";

interface Snapshot {
  data?: LatestFundQuote;
  loading: boolean;
  failed: boolean;
}
interface Resource {
  code: string;
  snapshot: Snapshot;
  listeners: Set<() => void>;
  expires: number;
  pending: boolean;
  refreshRequested?: boolean;
}
const initial: Snapshot = { loading: false, failed: false };
const resources = new Map<string, Resource>();
const queued = new Map<Resource, boolean>();
let scheduled = false;
let watchingFocus = false;
const completedEvent = "wealthsail:fund-quotes-updated";
export function subscribeLatestFundQuoteUpdates(listener: () => void) {
  window.addEventListener(completedEvent, listener);
  return () => window.removeEventListener(completedEvent, listener);
}
function publish(resource: Resource, patch: Partial<Snapshot>) {
  resource.snapshot = { ...resource.snapshot, ...patch };
  resource.listeners.forEach((listener) => listener());
}
async function fetchBatch(batch: Resource[], explicitRefresh: boolean) {
  batch.forEach((resource) => {
    resource.pending = true;
    publish(resource, { loading: true, failed: false });
  });
  try {
    const result = await (
      explicitRefresh ? refreshFundQuotes : getLatestFundQuotes
    )(batch.map((resource) => resource.code));
    for (const resource of batch) {
      const data = result.items.find((row) => row.fundCode === resource.code);
      if (!data) throw new Error("MissingFundQuote");
      resource.expires = Date.now() + 60000;
      publish(resource, { data, loading: false, failed: false });
    }
    window.dispatchEvent(new Event(completedEvent));
  } catch {
    batch.forEach((resource) => {
      resource.expires = Date.now() + 5000;
      publish(resource, { loading: false, failed: true });
    });
  } finally {
    batch.forEach((resource) => {
      resource.pending = false;
      const refreshRequested = resource.refreshRequested;
      resource.refreshRequested = false;
      if (refreshRequested && resource.listeners.size) {
        queue(resource, true);
      }
    });
  }
}
function queue(resource: Resource, explicitRefresh = false) {
  if (resource.pending) {
    if (explicitRefresh) resource.refreshRequested = true;
    return;
  }
  queued.set(resource, explicitRefresh || queued.get(resource) === true);
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(() => {
    scheduled = false;
    const batch = [...queued].filter(
      ([resource]) => resource.listeners.size && !resource.pending,
    );
    queued.clear();
    for (const explicit of [false, true]) {
      const group = batch
        .filter(([, refresh]) => refresh === explicit)
        .map(([resource]) => resource);
      for (let offset = 0; offset < group.length; offset += 32)
        void fetchBatch(group.slice(offset, offset + 32), explicit);
    }
  });
}
function refreshVisible() {
  if (document.visibilityState === "hidden") return;
  for (const resource of resources.values())
    if (resource.listeners.size) queue(resource);
}
export function refreshLatestFundQuotes() {
  if (document.visibilityState === "hidden") return;
  for (const resource of resources.values())
    if (resource.listeners.size) queue(resource, true);
}
function subscribe(code: string, listener: () => void) {
  if (!/^\d{6}$/.test(code)) return () => undefined;
  let resource = resources.get(code);
  if (!resource) {
    for (const [key, old] of resources)
      if (
        !old.listeners.size &&
        !old.pending &&
        (old.expires < Date.now() || resources.size > 128)
      )
        resources.delete(key);
    resource = {
      code,
      snapshot: initial,
      listeners: new Set(),
      expires: 0,
      pending: false,
    };
    resources.set(code, resource);
  }
  const reenter = !resource.listeners.size;
  resource.listeners.add(listener);
  if (reenter || resource.expires <= Date.now()) queue(resource);
  if (!watchingFocus) {
    window.addEventListener("focus", refreshVisible);
    document.addEventListener("visibilitychange", refreshVisible);
    watchingFocus = true;
  }
  const observed = resource;
  return () => {
    observed.listeners.delete(listener);
    if (![...resources.values()].some((row) => row.listeners.size)) {
      window.removeEventListener("focus", refreshVisible);
      document.removeEventListener("visibilitychange", refreshVisible);
      watchingFocus = false;
    }
  };
}
export function useLatestFundQuote(code?: string) {
  const identity = code ?? "";
  const observe = useCallback(
    (listener: () => void) => subscribe(identity, listener),
    [identity],
  );
  const snapshot = useCallback(
    () => resources.get(identity)?.snapshot ?? initial,
    [identity],
  );
  return useSyncExternalStore(observe, snapshot, () => initial);
}

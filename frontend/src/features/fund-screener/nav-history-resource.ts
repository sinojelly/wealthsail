import { useCallback, useSyncExternalStore } from "react";
import {
  getFundNavHistory,
  refreshFundNavHistory,
  type FundNavHistory,
  type FundNavPeriod,
} from "../../api/funds";

interface Snapshot {
  data?: FundNavHistory;
  loading: boolean;
  failed: boolean;
  exhausted: boolean;
}
interface Resource {
  key: string;
  code: string;
  period: FundNavPeriod;
  snapshot: Snapshot;
  listeners: Set<() => void>;
  controller?: AbortController;
  timer?: ReturnType<typeof setTimeout>;
  deadline: number;
  expires: number;
}
const initial: Snapshot = { loading: true, failed: false, exhausted: false };
const resources = new Map<string, Resource>();
const pending = (data?: FundNavHistory) =>
  data?.refresh.state === "QUEUED" || data?.refresh.state === "RUNNING";
function publish(resource: Resource, patch: Partial<Snapshot>) {
  resource.snapshot = { ...resource.snapshot, ...patch };
  resource.listeners.forEach((listener) => listener());
}
function verified(data: FundNavHistory, code: string, period: FundNavPeriod) {
  if (
    data?.fundCode !== code ||
    data.period !== period ||
    data.priceBasis !== "UNIT_NAV" ||
    data.unit !== "CNY/份" ||
    !Array.isArray(data.points) ||
    data.points.length > 20000 ||
    !data.coverage ||
    !data.summary ||
    !data.refresh
  )
    throw new Error("InvalidNavHistory");
  const cutoff = data.asOf?.slice(0, 10);
  if (data.points.length && (!cutoff || !/^\d{4}-\d{2}-\d{2}$/.test(cutoff)))
    throw new Error("MissingNavObservationDate");
  const seen = new Map<string, string>();
  for (const point of data.points) {
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(point.date) ||
      point.date > (cutoff ?? "") ||
      !Number.isFinite(Number(point.unitNav)) ||
      Number(point.unitNav) <= 0 ||
      (seen.has(point.date) &&
        Number(seen.get(point.date)) !== Number(point.unitNav))
    )
      throw new Error("InvalidNavPoint");
    seen.set(point.date, point.unitNav);
  }
  return {
    ...data,
    points: [...seen]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, unitNav]) => ({ date, unitNav })),
  };
}
function start(resource: Resource, explicitRefresh = false) {
  if (resource.controller || (!resource.listeners.size && !explicitRefresh))
    return;
  const controller = new AbortController();
  resource.controller = controller;
  if (resource.timer) clearTimeout(resource.timer);
  publish(resource, { loading: true, failed: false });
  void (explicitRefresh ? refreshFundNavHistory : getFundNavHistory)(
    resource.code,
    resource.period,
    controller.signal,
  )
    .then((raw) => {
      if (controller.signal.aborted || resources.get(resource.key) !== resource)
        return;
      const data = verified(raw, resource.code, resource.period);
      const exhausted = pending(data) && Date.now() >= resource.deadline;
      publish(resource, { data, loading: false, failed: false, exhausted });
      resource.expires = Date.now() + 60000;
      if (pending(data) && !exhausted && resource.listeners.size) {
        const delay = Math.max(
          1000,
          Math.min(10000, Number(data.refresh.pollAfterMs) || 2000),
        );
        if (Date.now() + delay <= resource.deadline)
          resource.timer = setTimeout(() => start(resource), delay);
        else publish(resource, { exhausted: true });
      }
    })
    .catch(() => {
      if (
        !controller.signal.aborted &&
        resources.get(resource.key) === resource
      ) {
        if (explicitRefresh) resource.expires = Date.now() + 5000;
        publish(resource, { loading: false, failed: true });
      }
    })
    .finally(() => {
      if (resource.controller === controller) resource.controller = undefined;
    });
}
function getResource(code: string, period: FundNavPeriod) {
  const key = `${code}:${period}`;
  let resource = resources.get(key);
  if (!resource) {
    for (const [oldKey, old] of resources)
      if (
        !old.listeners.size &&
        (old.expires <= Date.now() || resources.size >= 64)
      )
        resources.delete(oldKey);
    resource = {
      key,
      code,
      period,
      snapshot: initial,
      listeners: new Set(),
      deadline: Date.now() + 90000,
      expires: 0,
    };
    resources.set(key, resource);
  }
  return resource;
}
function subscribe(code: string, period: FundNavPeriod, listener: () => void) {
  const resource = getResource(code, period);
  const key = resource.key;
  const reenter = !resource.listeners.size;
  resource.listeners.add(listener);
  if (
    (resource.expires <= Date.now() ||
      (reenter &&
        !resource.snapshot.failed &&
        pending(resource.snapshot.data))) &&
    !resource.controller
  )
    start(resource);
  const observed = resource;
  return () => {
    observed.listeners.delete(listener);
    queueMicrotask(() => {
      if (observed.listeners.size) return;
      if (observed.timer) clearTimeout(observed.timer);
      observed.controller?.abort();
      if (
        observed.snapshot.loading ||
        pending(observed.snapshot.data) ||
        observed.snapshot.failed
      ) {
        if (resources.get(key) === observed) resources.delete(key);
      }
    });
  };
}
export function useFundNavHistory(
  code: string,
  period: FundNavPeriod,
  enabled = true,
) {
  const key = `${code}:${period}`;
  const observe = useCallback(
    (listener: () => void) =>
      enabled ? subscribe(code, period, listener) : () => undefined,
    [code, period, enabled],
  );
  const snapshot = useCallback(
    () => (enabled ? (resources.get(key)?.snapshot ?? initial) : initial),
    [key, enabled],
  );
  const value = useSyncExternalStore(observe, snapshot, () => initial);
  const retry = useCallback(() => {
    const resource = resources.get(key) ?? getResource(code, period);
    resource.deadline = Date.now() + 90000;
    publish(resource, { exhausted: false });
    start(resource, true);
  }, [key, code, period]);
  return { ...value, retry };
}

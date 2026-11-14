import { HttpError } from "./client";
import type { OpportunityContext } from "./investment";

const referenceKey = /^[a-f0-9]{64}$/;
function invalidTransport(): never {
  throw new HttpError(
    "展示报告数据不完整，请重新读取。",
    502,
    "INVALID_REPORT_TRANSPORT",
  );
}
function entries(value: object): Array<[string, unknown]> {
  const prototype = Object.getPrototypeOf(value);
  if (
    Object.getOwnPropertySymbols(value).length ||
    (Array.isArray(value)
      ? prototype !== Array.prototype
      : prototype !== Object.prototype && prototype !== null)
  )
    invalidTransport();
  const descriptors = Object.getOwnPropertyDescriptors(value);
  if (Array.isArray(value)) {
    const length = descriptors.length.value as number;
    if (Object.keys(descriptors).length !== length + 1) invalidTransport();
    return Array.from({ length }, (_, index) => {
      const descriptor = descriptors[String(index)];
      if (
        !descriptor ||
        !Object.hasOwn(descriptor, "value") ||
        !descriptor.enumerable
      )
        invalidTransport();
      return [String(index), descriptor.value];
    });
  }
  return Object.entries(descriptors).map(([key, descriptor]) => {
    if (!Object.hasOwn(descriptor, "value") || !descriptor.enumerable)
      invalidTransport();
    return [key, descriptor.value];
  });
}
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Lossless, copy-on-write decoding of complete saved-report JSON subtrees. */
export function hydrateOpportunityReportValues(
  context: OpportunityContext,
): OpportunityContext {
  // An inline legacy report may legitimately contain this business key. The
  // server falls back to inline in that case, so only a v2 table enables decoding.
  if (!Object.hasOwn(context, "reportValues")) return context;
  try {
    const descriptor = Object.getOwnPropertyDescriptor(context, "reportValues");
    if (
      !descriptor ||
      !Object.hasOwn(descriptor, "value") ||
      !record(descriptor.value)
    )
      invalidTransport();
    const table = Object.fromEntries(entries(descriptor.value));
    if (
      !Object.hasOwn(table, "version") ||
      !Object.hasOwn(table, "items") ||
      table.version !== "shared-v2" ||
      !record(table.items)
    )
      invalidTransport();
    const itemEntries = entries(table.items);
    const items = new Map(itemEntries);
    if (itemEntries.some(([key]) => !referenceKey.test(key)))
      invalidTransport();
    const resolved = new Map<string, unknown>();
    const resolving = new Set<string>();
    const memo = new WeakMap<object, unknown>();
    const active = new WeakSet<object>();
    function resolve(key: string): unknown {
      if (!items.has(key) || resolving.has(key)) invalidTransport();
      if (resolved.has(key)) return resolved.get(key);
      resolving.add(key);
      const value = project(items.get(key));
      resolving.delete(key);
      resolved.set(key, value);
      return value;
    }
    function project(node: unknown): unknown {
      if (
        node === null ||
        typeof node === "string" ||
        typeof node === "boolean"
      )
        return node;
      if (typeof node === "number")
        return Number.isFinite(node) ? node : invalidTransport();
      if (typeof node !== "object") invalidTransport();
      if (active.has(node)) invalidTransport();
      if (memo.has(node)) return memo.get(node);
      active.add(node);
      const children = entries(node);
      let result: unknown = node;
      if (
        !Array.isArray(node) &&
        children.some(([key]) => key === "reportValueRef")
      ) {
        if (
          children.length !== 1 ||
          typeof children[0][1] !== "string" ||
          !referenceKey.test(children[0][1])
        )
          invalidTransport();
        result = resolve(children[0][1]);
      } else {
        let changed = false;
        const projected = children.map(([key, value]) => {
          const next = project(value);
          changed ||= next !== value;
          return [key, next] as const;
        });
        if (changed)
          result = Array.isArray(node)
            ? projected.map(([, value]) => value)
            : Object.fromEntries(projected);
      }
      active.delete(node);
      memo.set(node, result);
      return result;
    }
    // Validate even unused values: malformed data must never lurk in a shared table.
    for (const [key] of itemEntries) resolve(key);
    for (const [key, value] of Object.entries(table))
      if (key !== "version" && key !== "items") project(value);
    const result = { ...context };
    delete (result as OpportunityContext & { reportValues?: unknown })
      .reportValues;
    const daily = context.dailyResearch;
    if (daily?.report !== undefined && daily.report !== null) {
      const report = project(daily.report);
      if (report !== daily.report)
        result.dailyResearch = {
          ...daily,
          report: report as typeof daily.report,
        };
    }
    return result;
  } catch {
    return invalidTransport();
  }
}

import { HttpError } from "./client";
import type { OpportunityContext } from "./investment";

const chartKey = /^[a-f0-9]{64}$/;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function completeSeries(value: unknown): value is Record<string, unknown> {
  return (
    record(value) &&
    Object.hasOwn(value, "points") &&
    Array.isArray(value.points)
  );
}

function invalidTransport(): never {
  throw new HttpError(
    "展示报告曲线数据不完整，请重新读取。",
    502,
    "INVALID_CHART_SERIES",
  );
}

// The wire table shares complete chart objects. Hydration only changes report
// references; every consumer keeps the same inline shape as the legacy response.
export function hydrateOpportunityChartSeries(
  context: OpportunityContext,
): OpportunityContext {
  const wire = context as OpportunityContext & { chartSeries?: unknown };
  const hasTable = Object.hasOwn(wire, "chartSeries");
  let items: Record<string, unknown> | undefined;
  if (hasTable) {
    const table = wire.chartSeries;
    if (
      !record(table) ||
      !Object.hasOwn(table, "version") ||
      table.version !== "shared-v1" ||
      !Object.hasOwn(table, "items") ||
      !record(table.items)
    )
      invalidTransport();
    items = table.items;
    for (const key of Object.keys(items)) {
      if (!chartKey.test(key) || !completeSeries(items[key]))
        invalidTransport();
    }
  }

  const visited = new WeakMap<object, unknown>();
  function project(node: unknown): unknown {
    if (node === null || typeof node !== "object") return node;
    if (visited.has(node)) return visited.get(node);
    if (Array.isArray(node)) {
      let updated: unknown[] | undefined;
      node.forEach((child, index) => {
        const next = project(child);
        if (next !== child) {
          updated ??= node.slice();
          updated[index] = next;
        }
      });
      const result = updated ?? node;
      visited.set(node, result);
      return result;
    }
    let updated: Record<string, unknown> | undefined;
    for (const key of Object.keys(node)) {
      const child = (node as Record<string, unknown>)[key];
      let next: unknown;
      if (key === "series" && record(child)) {
        if (
          Object.keys(child).length === 1 &&
          Object.hasOwn(child, "seriesRef")
        ) {
          const reference = child.seriesRef;
          if (
            typeof reference !== "string" ||
            !chartKey.test(reference) ||
            !items ||
            !Object.hasOwn(items, reference)
          )
            invalidTransport();
          next = items[reference];
        } else {
          // Complete inline legacy curves can contain large point arrays. Their
          // contents are chart data, not nested transport references.
          next = completeSeries(child) ? child : project(child);
        }
      } else next = project(child);
      if (next !== child) {
        updated ??= { ...node };
        // Spread retains an own __proto__ data property, so this assignment
        // updates that property without invoking Object.prototype's setter.
        updated[key] = next;
      }
    }
    const result = updated ?? node;
    visited.set(node, result);
    return result;
  }

  let result = wire;
  if (hasTable) {
    result = { ...wire };
    delete result.chartSeries;
  }
  const daily = context.dailyResearch;
  const report = daily?.report;
  if (record(report)) {
    const updated = project(report);
    if (updated !== report) {
      result = {
        ...result,
        dailyResearch: {
          ...daily!,
          report: updated as NonNullable<
            OpportunityContext["dailyResearch"]
          >["report"],
        },
      };
    }
  }
  return result;
}

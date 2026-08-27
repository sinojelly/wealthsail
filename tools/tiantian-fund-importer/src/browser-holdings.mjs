import { normalizeHolding, validateHoldingSnapshot } from "./holdings.mjs";

const HOLDINGS_URL = "https://trade.1234567.com.cn/myAssets/hold";
const CODE_KEYS = ["fundCode", "productCode", "FCODE", "code"];
const QUANTITY_KEYS = ["holdShare", "quantity", "holdQuantity", "shares"];
const VALUE_KEYS = ["marketValue", "holdValue", "assetValue"];
const COST_KEYS = ["costAmount", "totalCost", "holdCost", "averageCost", "avgCost"];

function firstValue(record, keys) {
  for (const key of keys) {
    if (record?.[key] != null && String(record[key]).trim() !== "") return record[key];
  }
  return null;
}

function resemblesHolding(record) {
  return (
    record &&
    typeof record === "object" &&
    /^\d{6}$/.test(String(firstValue(record, CODE_KEYS) || "").trim()) &&
    firstValue(record, QUANTITY_KEYS) != null &&
    firstValue(record, VALUE_KEYS) != null &&
    firstValue(record, COST_KEYS) != null
  );
}

function summaryFrom(container, rows) {
  const count = firstValue(container, ["totalCount", "count", "fundCount"]);
  const marketValue = firstValue(container, [
    "totalMarketValue",
    "totalAssetValue",
    "holdMarketValue",
  ]);
  return {
    totalCount: count == null ? rows.length : Number(count),
    totalMarketValue: marketValue == null ? null : String(marketValue),
  };
}

function findHoldingCollection(node, visited = new Set()) {
  if (!node || typeof node !== "object" || visited.has(node)) return null;
  visited.add(node);

  for (const [key, value] of Object.entries(node)) {
    if (!Array.isArray(value)) continue;
    if (value.length > 0 && value.every((record) => resemblesHolding(record))) {
      return { rows: value, summary: summaryFrom(node, value) };
    }
    if (
      value.length === 0 &&
      /hold|fund|position|asset|rows|list/i.test(key) &&
      Number(firstValue(node, ["totalCount", "count", "fundCount"])) === 0
    ) {
      return { rows: value, summary: summaryFrom(node, value) };
    }
  }
  for (const value of Object.values(node)) {
    const result = findHoldingCollection(value, visited);
    if (result) return result;
  }
  return null;
}

export async function captureHoldings(
  page,
  { capturedAt = new Date().toISOString(), timeoutMs = 30_000 } = {},
) {
  let settle;
  const candidatePromise = new Promise((resolve) => {
    settle = resolve;
  });
  const onResponse = async (response) => {
    try {
      const url = new URL(response.url());
      const contentType = response.headers()["content-type"] || "";
      if (!url.hostname.endsWith("1234567.com.cn") || !contentType.includes("json")) return;
      const candidate = findHoldingCollection(await response.json());
      if (candidate) settle(candidate);
    } catch {
      // A page loads many unrelated endpoints. Malformed/non-holdings responses
      // are ignored; the bounded timeout below owns the user-visible error.
    }
  };
  page.on("response", onResponse);
  let timer;
  try {
    await page.goto(HOLDINGS_URL, { waitUntil: "domcontentloaded", timeout: timeoutMs });
    const finalUrl = new URL(page.url());
    if (finalUrl.hostname.startsWith("login.")) {
      throw new Error("天天基金登录已过期，请在自动打开的浏览器中重新登录");
    }
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error("未捕获到结构化持仓数据，请确认持仓页已完整加载")),
        timeoutMs,
      );
    });
    const candidate = await Promise.race([candidatePromise, timeout]);
    const positions = candidate.rows.map((raw) => normalizeHolding(raw, capturedAt));
    return {
      positions,
      summary: candidate.summary,
      completeness: validateHoldingSnapshot(positions, candidate.summary),
    };
  } finally {
    clearTimeout(timer);
    page.off("response", onResponse);
  }
}

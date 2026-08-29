import { normalizeHolding, validateHoldingSnapshot } from "./holdings.mjs";

const HOLDINGS_URL = "https://trade.1234567.com.cn/myAssets/hold";
const CODE_KEYS = ["fundCode", "productCode", "FCODE", "code"];
const QUANTITY_KEYS = ["holdShare", "quantity", "holdQuantity", "shares"];
const VALUE_KEYS = ["marketValue", "holdValue", "assetValue"];
const COST_KEYS = ["costAmount", "totalCost", "holdCost", "averageCost", "avgCost"];
const DECIMAL = /^-?\d+(?:\.\d+)?$/;

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

function officialHoldingCollection(payload) {
  const result = payload?.succeed ? payload.result : null;
  if (!result || !Array.isArray(result.assetList)) return null;
  const rows = result.assetList.filter(
    (record) => /^\d{6}$/.test(String(record?.fundCode || "")) && record?.isHQB !== true,
  );
  const totalMarketValue = result.assetTotal?.[0]?.strValue;
  return {
    kind: "official",
    rows,
    summary: {
      totalCount: rows.length,
      totalMarketValue: totalMarketValue == null ? null : String(totalMarketValue),
    },
  };
}

function findHoldingCollection(node, visited = new Set()) {
  if (!node || typeof node !== "object" || visited.has(node)) return null;
  visited.add(node);

  const official = officialHoldingCollection(node);
  if (official) return official;

  for (const [key, value] of Object.entries(node)) {
    if (!Array.isArray(value)) continue;
    if (value.length > 0 && value.every((record) => resemblesHolding(record))) {
      return { kind: "direct", rows: value, summary: summaryFrom(node, value) };
    }
    if (
      value.length === 0 &&
      /hold|fund|position|asset|rows|list/i.test(key) &&
      Number(firstValue(node, ["totalCount", "count", "fundCount"])) === 0
    ) {
      return { kind: "direct", rows: value, summary: summaryFrom(node, value) };
    }
  }
  for (const value of Object.values(node)) {
    const result = findHoldingCollection(value, visited);
    if (result) return result;
  }
  return null;
}

function decimalText(value, label) {
  const text = String(value ?? "").trim().replaceAll(",", "");
  if (!DECIMAL.test(text)) throw new Error(`${label}不是合法十进制数：${text || "空"}`);
  return text;
}

function sumDecimalTexts(values, label) {
  const decimals = values.map((value) => decimalText(value, label));
  const scale = Math.max(0, ...decimals.map((value) => value.split(".")[1]?.length || 0));
  const total = decimals.reduce((sum, value) => {
    const negative = value.startsWith("-");
    const unsigned = negative ? value.slice(1) : value;
    const [integer, fraction = ""] = unsigned.split(".");
    const coefficient = BigInt(`${integer}${fraction.padEnd(scale, "0")}`);
    return sum + (negative ? -coefficient : coefficient);
  }, 0n);
  const sign = total < 0n ? "-" : "";
  const digits = (total < 0n ? -total : total).toString().padStart(scale + 1, "0");
  if (scale === 0) return `${sign}${digits}`;
  return `${sign}${digits.slice(0, -scale)}.${digits.slice(-scale)}`;
}

function decimalDifferenceText(left, right) {
  const values = [decimalText(left, "明细市值"), decimalText(right, "汇总市值")];
  const scale = Math.max(...values.map((value) => value.split(".")[1]?.length || 0));
  const coefficient = (value) => {
    const negative = value.startsWith("-");
    const unsigned = negative ? value.slice(1) : value;
    const [integer, fraction = ""] = unsigned.split(".");
    const result = BigInt(`${integer}${fraction.padEnd(scale, "0")}`);
    return negative ? -result : result;
  };
  const difference = coefficient(values[0]) - coefficient(values[1]);
  const absolute = difference < 0n ? -difference : difference;
  const digits = absolute.toString().padStart(scale + 1, "0");
  return scale === 0 ? digits : `${digits.slice(0, -scale)}.${digits.slice(-scale)}`;
}

function withinOneCent(left, right) {
  const values = [decimalText(left, "明细市值"), decimalText(right, "汇总市值")];
  const scale = Math.max(2, ...values.map((value) => value.split(".")[1]?.length || 0));
  const coefficient = (value) => {
    const negative = value.startsWith("-");
    const unsigned = negative ? value.slice(1) : value;
    const [integer, fraction = ""] = unsigned.split(".");
    const result = BigInt(`${integer}${fraction.padEnd(scale, "0")}`);
    return negative ? -result : result;
  };
  const difference = coefficient(values[0]) - coefficient(values[1]);
  const absolute = difference < 0n ? -difference : difference;
  return absolute <= 10n ** BigInt(scale - 2);
}

async function readOfficialHoldingDetail(page, source, timeoutMs) {
  const detailUrl = new URL("/myassets/single", HOLDINGS_URL);
  detailUrl.searchParams.set("iv", String(source.isVirtual ?? ""));
  detailUrl.searchParams.set("fc", String(source.fundCode));
  await page.goto(detailUrl.href, { waitUntil: "domcontentloaded", timeout: timeoutMs });
  if (new URL(page.url()).hostname.startsWith("login.")) {
    throw new Error("读取基金明细时登录已过期，请重新采集");
  }
  await page.waitForFunction(
    () =>
      [...document.querySelectorAll("table.fene tbody tr")].some(
        (row) => row.querySelectorAll(":scope > td").length >= 4,
      ),
    null,
    { timeout: timeoutMs },
  );
  const detail = await page.evaluate(() => {
    const table = document.querySelector("table.fene");
    return {
      headers: [...table.querySelectorAll("th")].map((cell) => cell.textContent.trim()),
      rows: [...table.querySelectorAll("tbody tr")]
        .map((row) =>
          [...row.querySelectorAll(":scope > td")].map((cell) => cell.textContent.trim()),
        )
        .filter((cells) => cells.length >= 4),
    };
  });
  const availableIndex = detail.headers.findIndex((value) => value.includes("可用份额"));
  const quantityIndex = detail.headers.findIndex((value) => value.includes("持仓份额"));
  const marketValueIndex = detail.headers.findIndex((value) => value.includes("持仓市值"));
  if (availableIndex < 0 || quantityIndex < 0 || marketValueIndex < 0 || !detail.rows.length) {
    throw new Error(`基金 ${source.fundCode} 持仓明细结构已变化，无法安全读取份额`);
  }
  const quantity = sumDecimalTexts(
    detail.rows.map((row) => row[quantityIndex]),
    "持仓份额",
  );
  const availableShare = sumDecimalTexts(
    detail.rows.map((row) => row[availableIndex]),
    "可用份额",
  );
  const marketValue = sumDecimalTexts(
    detail.rows.map((row) => row[marketValueIndex]),
    "持仓市值",
  );
  if (!withinOneCent(marketValue, source.assetValue)) {
    throw new Error(
      `基金 ${source.fundCode} 明细市值与持仓汇总不一致` +
        `（明细 ${detail.rows.length} 行，绝对差额 ${decimalDifferenceText(marketValue, source.assetValue)} 元）`,
    );
  }
  return {
    ...source,
    holdShare: quantity,
    availableShare,
    marketValue: decimalText(source.assetValue, "持仓汇总市值"),
    holdingProfit: source.profitValue,
    holdingProfitRate: source.profitPercent,
    navDate: source.navdate,
    bankCard: detail.rows[0][0],
  };
}

export async function captureHoldings(
  page,
  {
    capturedAt = new Date().toISOString(),
    timeoutMs = 30_000,
    prompt,
    logger = () => {},
    detailDelay = async () => {},
  } = {},
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
    let finalUrl = new URL(page.url());
    if (finalUrl.hostname.startsWith("login.")) {
      if (!prompt) {
        throw new Error("天天基金登录已过期，请在自动打开的浏览器中重新登录");
      }
      logger("");
      logger("持仓页需要单独确认登录。请在当前浏览器中完成登录或扫码。");
      await prompt("看到“我的基金”持仓页后，请按 Enter 继续：");
      await page.goto(HOLDINGS_URL, { waitUntil: "domcontentloaded", timeout: timeoutMs });
      finalUrl = new URL(page.url());
      if (finalUrl.hostname.startsWith("login.")) {
        throw new Error("持仓页登录未完成，请登录成功并看到持仓后再继续");
      }
    }
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(
        () => reject(new Error("未捕获到结构化持仓数据，请确认持仓页已完整加载")),
        timeoutMs,
      );
    });
    const candidate = await Promise.race([candidatePromise, timeout]);
    const positionSources = [];
    for (const [index, raw] of candidate.rows.entries()) {
      if (candidate.kind === "official") {
        logger(`正在读取基金持仓明细 ${index + 1}/${candidate.rows.length}`);
        positionSources.push(await readOfficialHoldingDetail(page, raw, timeoutMs));
        if (index + 1 < candidate.rows.length) await detailDelay();
      } else {
        positionSources.push(raw);
      }
    }
    const positions = positionSources.map((raw) => normalizeHolding(raw, capturedAt));
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

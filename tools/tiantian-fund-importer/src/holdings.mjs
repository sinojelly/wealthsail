const FUND_CODE = /^\d{6}$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function sourceText(raw, aliases) {
  for (const alias of aliases) {
    const value = raw?.[alias];
    if (value != null && String(value).trim() !== "") {
      return String(value).trim().replaceAll(",", "");
    }
  }
  return "";
}

function parseDecimal(value, label) {
  const text = String(value ?? "").trim().replaceAll(",", "");
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(text);
  if (!match) {
    throw new Error(`${label}不是合法十进制数：${text || "空"}`);
  }
  const fraction = match[3] || "";
  const digits = BigInt(`${match[2]}${fraction}`);
  return {
    coefficient: match[1] ? -digits : digits,
    scale: fraction.length,
    source: text,
  };
}

function pow10(exponent) {
  return 10n ** BigInt(exponent);
}

function formatScaled(coefficient, scale) {
  const sign = coefficient < 0n ? "-" : "";
  const digits = (coefficient < 0n ? -coefficient : coefficient)
    .toString()
    .padStart(scale + 1, "0");
  if (scale === 0) return `${sign}${digits}`;
  return `${sign}${digits.slice(0, -scale)}.${digits.slice(-scale)}`;
}

function rescale(decimal, targetScale) {
  if (decimal.scale === targetScale) return decimal.coefficient;
  if (decimal.scale < targetScale) {
    return decimal.coefficient * pow10(targetScale - decimal.scale);
  }
  const divisor = pow10(decimal.scale - targetScale);
  const quotient = decimal.coefficient / divisor;
  const remainder = decimal.coefficient % divisor;
  const rounded = (remainder < 0n ? -remainder : remainder) * 2n >= divisor;
  return quotient + (rounded ? (decimal.coefficient < 0n ? -1n : 1n) : 0n);
}

function divideDecimal(numeratorValue, denominatorValue, scale, multiplier = 1n) {
  const numerator = parseDecimal(numeratorValue, "分子");
  const denominator = parseDecimal(denominatorValue, "分母");
  if (denominator.coefficient === 0n) throw new Error("分母不能为零");

  const dividend =
    numerator.coefficient * multiplier * pow10(denominator.scale + scale);
  const divisor = denominator.coefficient * pow10(numerator.scale);
  const quotient = dividend / divisor;
  const remainder = dividend % divisor;
  const absoluteRemainder = remainder < 0n ? -remainder : remainder;
  const absoluteDivisor = divisor < 0n ? -divisor : divisor;
  const sameSign = (dividend < 0n) === (divisor < 0n);
  const rounded = absoluteRemainder * 2n >= absoluteDivisor;
  return formatScaled(quotient + (rounded ? (sameSign ? 1n : -1n) : 0n), scale);
}

function subtractDecimal(leftValue, rightValue, scale = 2) {
  const left = parseDecimal(leftValue, "被减数");
  const right = parseDecimal(rightValue, "减数");
  return formatScaled(rescale(left, scale) - rescale(right, scale), scale);
}

function multiplyDecimal(leftValue, rightValue, maximumScale = 8) {
  const left = parseDecimal(leftValue, "乘数");
  const right = parseDecimal(rightValue, "乘数");
  const naturalScale = left.scale + right.scale;
  const targetScale = Math.min(naturalScale, maximumScale);
  const product = { coefficient: left.coefficient * right.coefficient, scale: naturalScale };
  let result = formatScaled(rescale(product, targetScale), targetScale);
  if (result.includes(".")) {
    result = result.replace(/0+$/, "").replace(/\.$/, "");
  }
  return result;
}

function maskAccount(value) {
  const digits = String(value ?? "").replace(/\D/g, "");
  return digits ? `****${digits.slice(-4)}` : "";
}

function optionalDecimal(raw, aliases, label) {
  const value = sourceText(raw, aliases);
  if (!value) return null;
  parseDecimal(value, label);
  return value;
}

export function normalizeHolding(raw, capturedAt) {
  const productCode = sourceText(raw, ["fundCode", "productCode", "FCODE", "code"]);
  if (!FUND_CODE.test(productCode)) {
    throw new Error(`基金代码必须是六位数字：${productCode || "空"}`);
  }
  const productName = sourceText(raw, ["fundName", "productName", "SHORTNAME", "name"]);
  const quantity = sourceText(raw, ["holdShare", "quantity", "holdQuantity", "shares"]);
  const parsedQuantity = parseDecimal(quantity, "持有份额");
  if (parsedQuantity.coefficient <= 0n) throw new Error("持有份额必须大于零");

  const directAverageCost = sourceText(raw, ["averageCost", "avgCost", "costNav"]);
  const sourceCostAmount = sourceText(raw, ["costAmount", "totalCost", "holdCost"]);
  if (!directAverageCost && !sourceCostAmount) {
    throw new Error(`基金 ${productCode} 缺少可靠成本，不能生成零成本基线`);
  }
  if (sourceCostAmount) parseDecimal(sourceCostAmount, "成本总额");
  const averageCost = directAverageCost || divideDecimal(sourceCostAmount, quantity, 8);
  parseDecimal(averageCost, "平均成本");
  const costAmount = sourceCostAmount || multiplyDecimal(averageCost, quantity);

  const marketValue = sourceText(raw, ["marketValue", "holdValue", "assetValue"]);
  parseDecimal(marketValue, "当前市值");
  const holdingProfit =
    optionalDecimal(raw, ["holdingProfit", "holdProfit", "profit"], "持有收益") ||
    subtractDecimal(marketValue, costAmount, 2);
  const holdingProfitRate =
    optionalDecimal(raw, ["holdingProfitRate", "holdProfitRate", "profitRate"], "持有收益率") ||
    divideDecimal(holdingProfit, costAmount, 2, 100n);

  const latestNavDate = sourceText(raw, ["navDate", "latestNavDate", "PDATE"]);
  if (latestNavDate && !ISO_DATE.test(latestNavDate)) {
    throw new Error(`净值日期格式无效：${latestNavDate}`);
  }

  return {
    sourceProvider: "TIANTIAN_FUND",
    sourceRecordId: sourceText(raw, ["id", "sourceRecordId", "holdingId"]) || productCode,
    productCode,
    productName,
    quantity,
    availableQuantity: optionalDecimal(
      raw,
      ["availableShare", "availableQuantity", "usableShare"],
      "可用份额",
    ),
    averageCost,
    costAmount,
    marketValue,
    holdingProfit,
    holdingProfitRate,
    latestNav: optionalDecimal(raw, ["nav", "latestNav", "DWJZ"], "最新净值"),
    latestNavDate,
    accountMask: maskAccount(sourceText(raw, ["bankCard", "bankAccount", "bkAcc"])),
    capturedAt,
  };
}

export function validateHoldingSnapshot(rows, summary = {}) {
  const seen = new Set();
  for (const row of rows) {
    if (seen.has(row.productCode)) {
      throw new Error(`当前持仓出现重复基金代码 ${row.productCode}`);
    }
    seen.add(row.productCode);
  }

  const reasons = [];
  if (summary.totalCount != null && Number(summary.totalCount) !== rows.length) {
    reasons.push("POSITION_COUNT_MISMATCH");
  }
  if (summary.totalMarketValue != null && String(summary.totalMarketValue).trim() !== "") {
    const expected = rescale(parseDecimal(summary.totalMarketValue, "持仓总市值"), 2);
    const actual = rows.reduce(
      (total, row) => total + rescale(parseDecimal(row.marketValue, "当前市值"), 2),
      0n,
    );
    if (expected !== actual) reasons.push("MARKET_VALUE_MISMATCH");
  }

  return {
    completeness: reasons.length ? "PARTIAL" : "COMPLETE",
    reasons,
    positionCount: rows.length,
  };
}

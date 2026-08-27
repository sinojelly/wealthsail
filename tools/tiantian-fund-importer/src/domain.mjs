const POSTING_STATUS = "成功";
const HUOQIBAO_CASH_ONLY_BUSINESS_TYPES = new Set([
  "活期宝充值",
  "活期宝自动充值",
  "活期宝普通取现",
  "活期宝快速取现",
  "活期宝转出",
  "活期宝互转",
  "活期宝结转收益",
  "活期宝预约取现",
  "活期宝信用卡还款",
  "活期宝每日分红",
]);
const SPECIAL_FUND_ADJUSTMENT_TYPES = new Set(["强行调增", "活动发放"]);

export function normalizeFundBusinessType(value) {
  const businessType = text(value);
  if (["强行调增", "活动发放"].includes(businessType)) return "FORCED_ADJUSTMENT";
  if (/转换.*转入|转入.*转换/.test(businessType)) return "CONVERSION_IN";
  if (/转换.*转出|转出.*转换/.test(businessType)) return "CONVERSION_OUT";
  if (/红利再投资/.test(businessType)) return "DIVIDEND_REINVEST";
  if (/现金分红/.test(businessType)) return "DIVIDEND_CASH";
  if (/卖出|赎回/.test(businessType)) return "REDEEM";
  if (/买入|活期宝转入/.test(businessType)) return "PURCHASE";
  return "UNKNOWN";
}

function text(value) {
  return value == null ? "" : String(value).trim();
}

function hasFundCode(value) {
  return /^\d{6}$/.test(text(value));
}

export function classifyTransaction(raw) {
  const status = text(raw.appStateText ?? raw.status);
  if (status !== POSTING_STATUS) {
    return { category: "NON_POSTING", reason: `STATUS_${status || "UNKNOWN"}` };
  }

  const productName = text(raw.productShowName ?? raw.productName);
  if (productName === "活期宝") {
    return { category: "CASH_MANAGEMENT", reason: "HUOQIBAO_PRODUCT" };
  }

  const businessType = text(raw.businessTypeText1 ?? raw.businessType);
  if (HUOQIBAO_CASH_ONLY_BUSINESS_TYPES.has(businessType)) {
    return { category: "CASH_MANAGEMENT", reason: "HUOQIBAO_CASH_ONLY_BUSINESS" };
  }
  if (SPECIAL_FUND_ADJUSTMENT_TYPES.has(businessType)) {
    return { category: "REVIEW_REQUIRED", reason: "SPECIAL_FUND_ADJUSTMENT" };
  }

  // 关联账户可能是活期宝，但只要交易对象有真实基金代码，它仍然是基金交易。
  if (hasFundCode(raw.productCode)) {
    return { category: "FUND_TRADE", reason: "FUND_PRODUCT_CODE" };
  }

  if (businessType.includes("活期宝")) {
    return { category: "CASH_MANAGEMENT", reason: "HUOQIBAO_BUSINESS" };
  }

  return { category: "REVIEW_REQUIRED", reason: "UNRECOGNIZED_SUCCESS" };
}

function maskAccount(rawAccount, accountTitle) {
  const raw = text(rawAccount);
  const digits = raw.replace(/\D/g, "");
  if (!digits) {
    return text(accountTitle || raw);
  }

  const labelFromAccount = raw.replace(/[|｜]?\s*\d[\d\s*-]*$/, "").trim();
  const label = text(accountTitle) || labelFromAccount;
  const suffix = digits.slice(-4);
  return label ? `${label} | ****${suffix}` : `****${suffix}`;
}

export function normalizeTransaction(raw, context = {}) {
  const classification = classifyTransaction(raw);
  const sourceId = text(raw.id);
  const businessCode = text(raw.businessCode);
  const traceNo = text(raw.traceNo);

  return {
    sourceProvider: "TIANTIAN_FUND",
    sourceId,
    traceNo,
    occurredAt: text(raw.strikeStartDate),
    productCode: text(raw.productCode),
    productName: text(raw.productShowName),
    businessCode,
    businessType: text(raw.businessTypeText1),
    normalizedBusinessType: normalizeFundBusinessType(raw.businessTypeText1),
    applicationValue: text(raw.applyCount),
    applicationUnit: text(raw.applyCountUnit),
    confirmationValue: text(raw.confirmCount),
    confirmationUnit: text(raw.confirmCountUnit),
    accountMask: maskAccount(raw.bkAcc, raw.hqbTitle),
    status: text(raw.appStateText),
    category: classification.category,
    classificationReason: classification.reason,
    detailRef: [sourceId, businessCode, traceNo].join(":"),
    sourceWindowStart: text(context.windowStart),
    sourceWindowEnd: text(context.windowEnd),
    sourcePage: Number(context.page || 1),
  };
}

function deduplicationKey(record) {
  if (text(record.sourceId)) {
    return `id:${text(record.sourceId)}`;
  }
  return [
    "fallback",
    text(record.occurredAt),
    text(record.productCode),
    text(record.businessType),
    text(record.applicationValue),
    text(record.applicationUnit),
    text(record.confirmationValue),
    text(record.confirmationUnit),
    text(record.accountMask),
  ].join("|");
}

export function deduplicateTransactions(records) {
  const seen = new Set();
  const unique = [];
  let duplicateCount = 0;

  for (const record of records) {
    const key = deduplicationKey(record);
    if (seen.has(key)) {
      duplicateCount += 1;
      continue;
    }
    seen.add(key);
    unique.push(record);
  }

  return { records: unique, duplicateCount };
}

function parseIsoDate(value) {
  const source = text(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(source)) {
    throw new Error(`无效日期：${source}`);
  }
  const [year, month, day] = source.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    throw new Error(`无效日期：${source}`);
  }
  return date;
}

function formatIsoDate(date) {
  return date.toISOString().slice(0, 10);
}

function addUtcDays(date, days) {
  const result = new Date(date);
  result.setUTCDate(result.getUTCDate() + days);
  return result;
}

function addOneCalendarYearClamped(date) {
  const year = date.getUTCFullYear() + 1;
  const month = date.getUTCMonth();
  const day = date.getUTCDate();
  const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  return new Date(Date.UTC(year, month, Math.min(day, lastDay)));
}

export function splitDateRange(startValue, endValue) {
  const start = parseIsoDate(startValue);
  const end = parseIsoDate(endValue);
  if (start > end) {
    throw new Error("开始日期不能晚于结束日期");
  }

  const windows = [];
  let cursor = start;
  while (cursor <= end) {
    const maximumEnd = addUtcDays(addOneCalendarYearClamped(cursor), -1);
    const windowEnd = maximumEnd < end ? maximumEnd : end;
    windows.push({ start: formatIsoDate(cursor), end: formatIsoDate(windowEnd) });
    cursor = addUtcDays(windowEnd, 1);
  }
  return windows;
}

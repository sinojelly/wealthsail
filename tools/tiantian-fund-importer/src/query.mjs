import { splitDateRange } from "./domain.mjs";

// 来自天天基金交易查询页面第一行“业务类型”的 data-init；不包含活期宝业务编号。
export const FUND_BUSINESS_TYPES = Object.freeze([
  { code: "1", label: "买入" },
  { code: "2", label: "卖出" },
  { code: "3", label: "转换" },
  { code: "14", label: "超级转换" },
  { code: "4", label: "定投" },
  { code: "16", label: "组合调仓" },
  { code: "5", label: "分红" },
  { code: "13", label: "其他" },
]);

function isoDate(date) {
  return date.toISOString().slice(0, 10);
}

function dateFromIso(value) {
  // splitDateRange 同时承担严格日期校验。
  splitDateRange(value, value);
  return new Date(`${value}T00:00:00.000Z`);
}

function addDays(date, days) {
  const result = new Date(date);
  result.setUTCDate(result.getUTCDate() + days);
  return result;
}

function oneYearBefore(date) {
  const targetYear = date.getUTCFullYear() - 1;
  const month = date.getUTCMonth();
  const day = date.getUTCDate();
  const lastDay = new Date(Date.UTC(targetYear, month + 1, 0)).getUTCDate();
  return new Date(Date.UTC(targetYear, month, Math.min(day, lastDay)));
}

export function buildQueryPlans({ from, to, today }) {
  const fromDate = dateFromIso(from);
  const toDate = dateFromIso(to);
  const todayDate = dateFromIso(today);
  if (fromDate > toDate) {
    throw new Error("开始日期不能晚于结束日期");
  }
  if (toDate > todayDate) {
    throw new Error("结束日期不能晚于今天");
  }

  const recentStart = oneYearBefore(todayDate);
  const windows = [];
  if (fromDate < recentStart) {
    const historicalEnd = toDate < recentStart ? toDate : addDays(recentStart, -1);
    for (const window of splitDateRange(from, isoDate(historicalEnd))) {
      windows.push({ ...window, isHistory: true });
    }
  }
  if (toDate >= recentStart) {
    const start = fromDate > recentStart ? fromDate : recentStart;
    windows.push({ start: isoDate(start), end: to, isHistory: false });
  }

  return windows.flatMap((window) =>
    FUND_BUSINESS_TYPES.map((businessType) => ({
      windowStart: window.start,
      windowEnd: window.end,
      isHistory: window.isHistory,
      busType: businessType.code,
      businessTypeLabel: businessType.label,
      status: "2",
    })),
  );
}

export function createQueryPayload(plan, pageNum, pageSize) {
  return {
    dataType: 1,
    startDate: `${plan.windowStart} 00:00:00`,
    endDate: `${plan.windowEnd} 00:00:00`,
    timeType: plan.isHistory ? "4" : "3",
    busType: plan.busType,
    status: plan.status,
    account: "",
    fundType: "0",
    pageSize,
    pageNum,
    container: "tb_delegate",
    fundCode: "",
    isHistory: plan.isHistory,
  };
}

export function accessTokenFromRequest(request) {
  try {
    const url = new URL(request.url());
    if (url.hostname !== "query.1234567.com.cn" || !url.pathname.startsWith("/queryapi/")) {
      return null;
    }
    return request.headers()["access-token"] || null;
  } catch {
    return null;
  }
}

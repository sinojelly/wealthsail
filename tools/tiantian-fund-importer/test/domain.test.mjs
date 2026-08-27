import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyTransaction,
  deduplicateTransactions,
  normalizeFundBusinessType,
  normalizeTransaction,
  splitDateRange,
} from "../src/domain.mjs";

test("活期宝每日分红不会进入基金交易账本", () => {
  const classification = classifyTransaction({
    productShowName: "活期宝",
    productCode: "",
    businessTypeText1: "活期宝每日分红",
    appStateText: "成功",
  });

  assert.deepEqual(classification, {
    category: "CASH_MANAGEMENT",
    reason: "HUOQIBAO_PRODUCT",
  });
});

test("使用活期宝付款的普通基金买入仍然是基金交易", () => {
  const classification = classifyTransaction({
    productShowName: "天弘中证食品饮料ETF联接C",
    productCode: "001632",
    businessTypeText1: "活期宝转入",
    hqbTitle: "活期宝",
    appStateText: "成功",
  });

  assert.deepEqual(classification, {
    category: "FUND_TRADE",
    reason: "FUND_PRODUCT_CODE",
  });
});

test("活期宝充值和取现即使带底层货币基金代码也属于现金管理", () => {
  for (const businessTypeText1 of ["活期宝充值", "活期宝快速取现", "活期宝普通取现"]) {
    assert.deepEqual(
      classifyTransaction({
        productShowName: "某货币市场基金",
        productCode: "000198",
        businessTypeText1,
        appStateText: "成功",
      }),
      { category: "CASH_MANAGEMENT", reason: "HUOQIBAO_CASH_ONLY_BUSINESS" },
    );
  }
  assert.deepEqual(
    classifyTransaction({
      productName: "某货币市场基金",
      productCode: "000198",
      businessType: "活期宝充值",
      status: "成功",
    }),
    { category: "CASH_MANAGEMENT", reason: "HUOQIBAO_CASH_ONLY_BUSINESS" },
  );
});

test("失败和撤单记录只用于对账而不进入持仓", () => {
  for (const status of ["失败", "已撤单", "在途"]) {
    assert.deepEqual(
      classifyTransaction({
        productShowName: "某基金",
        productCode: "000001",
        businessTypeText1: "买入",
        appStateText: status,
      }),
      { category: "NON_POSTING", reason: `STATUS_${status}` },
    );
  }
});

test("无法识别且没有基金代码的成功记录必须人工复核", () => {
  assert.deepEqual(
    classifyTransaction({
      productShowName: "未知产品",
      productCode: "",
      businessTypeText1: "其他",
      appStateText: "成功",
    }),
    { category: "REVIEW_REQUIRED", reason: "UNRECOGNIZED_SUCCESS" },
  );
});

test("强行调增和活动发放即使有基金代码也必须人工复核", () => {
  for (const businessTypeText1 of ["强行调增", "活动发放"]) {
    assert.deepEqual(
      classifyTransaction({
        productShowName: "某基金",
        productCode: "000001",
        businessTypeText1,
        appStateText: "成功",
      }),
      { category: "REVIEW_REQUIRED", reason: "SPECIAL_FUND_ADJUSTMENT" },
    );
  }
});

test("标准化交易保留精度并只保留账户尾号", () => {
  const normalized = normalizeTransaction(
    {
      id: "order-1",
      traceNo: "trace-1",
      businessCode: "022",
      strikeStartDate: "2025-08-10 12:38:18",
      productCode: "001632",
      productShowName: "天弘中证食品饮料ETF联接C",
      businessTypeText1: "买入",
      applyCount: "10000.00",
      applyCountUnit: "元",
      confirmCount: "3801.70",
      confirmCountUnit: "份",
      bkAcc: "6222021234562779",
      hqbTitle: "中国工商银行",
      appStateText: "成功",
    },
    { windowStart: "2025-01-01", windowEnd: "2025-12-31", page: 2 },
  );

  assert.equal(normalized.sourceId, "order-1");
  assert.equal(normalized.applicationValue, "10000.00");
  assert.equal(normalized.confirmationValue, "3801.70");
  assert.equal(normalized.accountMask, "中国工商银行 | ****2779");
  assert.equal(normalized.category, "FUND_TRADE");
  assert.equal(normalized.sourcePage, 2);
  assert.equal(normalized.detailRef, "order-1:022:trace-1");
});

test("去重优先使用来源订单号并保留首次出现顺序", () => {
  const records = [
    { sourceId: "A", occurredAt: "2025-01-01", productCode: "000001" },
    { sourceId: "B", occurredAt: "2025-01-02", productCode: "000002" },
    { sourceId: "A", occurredAt: "2025-01-01", productCode: "000001" },
  ];

  const result = deduplicateTransactions(records);

  assert.deepEqual(result.records, records.slice(0, 2));
  assert.equal(result.duplicateCount, 1);
});

test("日期区间按最多一个日历年连续切分且不重叠", () => {
  assert.deepEqual(splitDateRange("2020-02-29", "2022-03-01"), [
    { start: "2020-02-29", end: "2021-02-27" },
    { start: "2021-02-28", end: "2022-02-27" },
    { start: "2022-02-28", end: "2022-03-01" },
  ]);
});

test("无效日期区间会被拒绝", () => {
  assert.throws(() => splitDateRange("2025-01-02", "2025-01-01"), /开始日期不能晚于结束日期/);
  assert.throws(() => splitDateRange("not-a-date", "2025-01-01"), /无效日期/);
});

test("把基金业务文案标准化为持仓份额语义", () => {
  assert.equal(normalizeFundBusinessType("银行卡支付买入"), "PURCHASE");
  assert.equal(normalizeFundBusinessType("活期宝转入"), "PURCHASE");
  assert.equal(normalizeFundBusinessType("卖出回活期宝"), "REDEEM");
  assert.equal(normalizeFundBusinessType("现金分红"), "DIVIDEND_CASH");
  assert.equal(normalizeFundBusinessType("红利再投资"), "DIVIDEND_REINVEST");
  assert.equal(normalizeFundBusinessType("基金转换转入"), "CONVERSION_IN");
  assert.equal(normalizeFundBusinessType("基金转换转出"), "CONVERSION_OUT");
  assert.equal(normalizeFundBusinessType("强行调增"), "FORCED_ADJUSTMENT");
  assert.equal(normalizeFundBusinessType("无法识别业务"), "UNKNOWN");
});

test("标准化交易输出包含版本化业务类型而不改变原始业务文案", () => {
  const result = normalizeTransaction({
    id: "tx-1",
    strikeStartDate: "2026-08-20",
    productCode: "110022",
    productShowName: "易方达消费行业股票",
    businessTypeText1: "卖出回银行卡",
    applyCount: "100.00",
    applyCountUnit: "份",
    confirmCount: "200.00",
    confirmCountUnit: "元",
    appStateText: "成功",
  });

  assert.equal(result.businessType, "卖出回银行卡");
  assert.equal(result.normalizedBusinessType, "REDEEM");
});

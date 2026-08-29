import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeHolding,
  validateHoldingSnapshot,
} from "../src/holdings.mjs";

const CAPTURED_AT = "2026-08-28T00:00:00+08:00";

function sourceHolding(overrides = {}) {
  return {
    id: "holding-110022",
    fundCode: "110022",
    fundName: "易方达消费行业股票",
    holdShare: "1234.56",
    availableShare: "1200.00",
    costAmount: "2500.00",
    marketValue: "2700.00",
    nav: "2.1876",
    navDate: "2026-08-27",
    bankCard: "6222000012345678",
    ...overrides,
  };
}

test("标准化当前基金持仓时保持十进制精度且不泄露完整银行卡", () => {
  assert.deepEqual(normalizeHolding(sourceHolding(), CAPTURED_AT), {
    sourceProvider: "TIANTIAN_FUND",
    sourceRecordId: "holding-110022",
    productCode: "110022",
    productName: "易方达消费行业股票",
    quantity: "1234.56",
    availableQuantity: "1200.00",
    averageCost: "2.02501296",
    costAmount: "2500.00",
    marketValue: "2700.00",
    holdingProfit: "200.00",
    holdingProfitRate: "8.00",
    latestNav: "2.1876",
    latestNavDate: "2026-08-27",
    accountMask: "****5678",
    capturedAt: CAPTURED_AT,
  });
});

test("来源直接提供平均成本时优先保留来源十进制值", () => {
  const result = normalizeHolding(
    sourceHolding({ averageCost: "2.0100", costAmount: "2481.4656" }),
    CAPTURED_AT,
  );

  assert.equal(result.averageCost, "2.0100");
  assert.equal(result.costAmount, "2481.4656");
});

test("只有平均成本时用份额精确推导成本总额", () => {
  const result = normalizeHolding(
    sourceHolding({ averageCost: "2.0100", costAmount: "", marketValue: "2700.00" }),
    CAPTURED_AT,
  );

  assert.equal(result.averageCost, "2.0100");
  assert.equal(result.costAmount, "2481.4656");
  assert.equal(result.holdingProfit, "218.53");
  assert.equal(result.holdingProfitRate, "8.81");
});

test("天天基金提供同时点市值和持仓收益时精确推导成本", () => {
  const result = normalizeHolding(
    sourceHolding({
      costAmount: "",
      averageCost: "",
      holdingProfit: "200.00",
    }),
    CAPTURED_AT,
  );

  assert.equal(result.costAmount, "2500.00");
  assert.equal(result.averageCost, "2.02501296");
  assert.equal(result.holdingProfit, "200.00");
});

test("当前持仓缺少成本事实时拒绝生成零成本基线", () => {
  assert.throws(
    () => normalizeHolding(sourceHolding({ costAmount: "", averageCost: "" }), CAPTURED_AT),
    /缺少可靠成本/,
  );
});

test("持仓快照条数与页面汇总不一致时标记为不完整", () => {
  const rows = [normalizeHolding(sourceHolding(), CAPTURED_AT)];

  const result = validateHoldingSnapshot(rows, {
    totalCount: 2,
    totalMarketValue: "2700.00",
  });

  assert.equal(result.completeness, "PARTIAL");
  assert.deepEqual(result.reasons, ["POSITION_COUNT_MISMATCH"]);
});

test("完整快照通过条数和市值汇总核对", () => {
  const rows = [normalizeHolding(sourceHolding(), CAPTURED_AT)];

  const result = validateHoldingSnapshot(rows, {
    totalCount: 1,
    totalMarketValue: "2700.00",
  });

  assert.deepEqual(result, {
    completeness: "COMPLETE",
    reasons: [],
    positionCount: 1,
  });
});

test("同一基金代码重复出现时拒绝形成含糊的权威快照", () => {
  const rows = [
    normalizeHolding(sourceHolding(), CAPTURED_AT),
    normalizeHolding(sourceHolding({ id: "duplicate" }), CAPTURED_AT),
  ];

  assert.throws(
    () => validateHoldingSnapshot(rows, { totalCount: 2, totalMarketValue: "5400.00" }),
    /重复基金代码 110022/,
  );
});

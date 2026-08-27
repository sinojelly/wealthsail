import assert from "node:assert/strict";
import test from "node:test";

import {
  accessTokenFromRequest,
  buildQueryPlans,
  createQueryPayload,
} from "../src/query.mjs";

test("查询计划只使用普通基金业务过滤器并跨越近一年边界", () => {
  const plans = buildQueryPlans({
    from: "2024-01-01",
    to: "2026-08-27",
    today: "2026-08-27",
  });

  const windows = [...new Set(plans.map((plan) => `${plan.windowStart}/${plan.windowEnd}/${plan.isHistory}`))];
  assert.deepEqual(windows, [
    "2024-01-01/2024-12-31/true",
    "2025-01-01/2025-08-26/true",
    "2025-08-27/2026-08-27/false",
  ]);

  const businessTypes = [...new Set(plans.map((plan) => plan.busType))];
  assert.deepEqual(businessTypes, ["1", "2", "3", "14", "4", "16", "5", "13"]);
  assert.ok(plans.every((plan) => plan.status === "2"));
  assert.ok(!businessTypes.some((value) => ["6", "7", "8", "9", "10", "12", "22", "23", "24"].includes(value)));
});

test("查询请求符合天天基金 DelegateList 的字段契约", () => {
  const payload = createQueryPayload(
    {
      windowStart: "2025-01-01",
      windowEnd: "2025-08-26",
      busType: "1",
      status: "2",
      isHistory: true,
    },
    3,
    20,
  );

  assert.deepEqual(payload, {
    dataType: 1,
    startDate: "2025-01-01 00:00:00",
    endDate: "2025-08-26 00:00:00",
    timeType: "4",
    busType: "1",
    status: "2",
    account: "",
    fundType: "0",
    pageSize: 20,
    pageNum: 3,
    container: "tb_delegate",
    fundCode: "",
    isHistory: true,
  });
});

test("访问令牌只从天天基金查询接口请求头中读取", () => {
  assert.equal(
    accessTokenFromRequest({
      url: () => "https://query.1234567.com.cn/queryapi/trading/Query/index",
      headers: () => ({ "access-token": "secret-in-memory" }),
    }),
    "secret-in-memory",
  );
  assert.equal(
    accessTokenFromRequest({
      url: () => "https://example.invalid/queryapi/trading/Query/index",
      headers: () => ({ "access-token": "must-not-read" }),
    }),
    null,
  );
});

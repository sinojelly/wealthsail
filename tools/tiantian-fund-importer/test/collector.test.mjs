import assert from "node:assert/strict";
import test from "node:test";

import { collectTransactions } from "../src/collector.mjs";

test("采集器按照总记录数自动获取后续分页", async () => {
  const requestedPages = [];
  const requestPage = async (query) => {
    requestedPages.push(query.pageNum);
    const count = query.pageNum === 1 ? 20 : 1;
    return {
      code: 1200,
      data: {
        totalCount: 21,
        list: Array.from({ length: count }, (_, index) => ({
          id: `order-${query.pageNum}-${index}`,
          strikeStartDate: "2025-01-01 10:00:00",
          productCode: "001632",
          productShowName: "某基金",
          businessTypeText1: "买入",
          appStateText: "成功",
        })),
      },
    };
  };

  const result = await collectTransactions({
    plans: [{ windowStart: "2025-01-01", windowEnd: "2025-12-31", busType: "1" }],
    requestPage,
    delay: async () => {},
  });

  assert.deepEqual(requestedPages, [1, 2]);
  assert.equal(result.length, 21);
  assert.equal(result[0].sourcePage, 1);
  assert.equal(result[20].sourcePage, 2);
});

test("接口拒绝访问时停止采集且不返回部分结果", async () => {
  await assert.rejects(
    collectTransactions({
      plans: [{ windowStart: "2025-01-01", windowEnd: "2025-12-31", busType: "1" }],
      requestPage: async () => ({ code: 1401, message: "登录已过期" }),
      delay: async () => {},
    }),
    /登录已过期或访问令牌失效/,
  );
});

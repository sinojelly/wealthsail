import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { captureHoldings } from "../src/browser-holdings.mjs";

class FakePage extends EventEmitter {
  constructor({ finalUrl, payload }) {
    super();
    this.finalUrl = finalUrl;
    this.payload = payload;
  }

  async goto() {
    if (this.payload) {
      queueMicrotask(() =>
        this.emit("response", {
          url: () => "https://trade.1234567.com.cn/api/assets/holdings",
          headers: () => ({ "content-type": "application/json; charset=utf-8" }),
          json: async () => this.payload,
        }),
      );
    }
  }

  url() {
    return this.finalUrl;
  }
}

test("从登录后的结构化响应提取持仓和同层汇总", async () => {
  const page = new FakePage({
    finalUrl: "https://trade.1234567.com.cn/myAssets/hold",
    payload: {
      code: 0,
      data: {
        totalCount: 1,
        totalMarketValue: "2700.00",
        rows: [
          {
            id: "holding-110022",
            fundCode: "110022",
            fundName: "易方达消费行业股票",
            holdShare: "1234.56",
            costAmount: "2500.00",
            marketValue: "2700.00",
            nav: "2.1876",
            navDate: "2026-08-27",
          },
        ],
      },
    },
  });

  const result = await captureHoldings(page, {
    capturedAt: "2026-08-28T00:00:00+08:00",
    timeoutMs: 100,
  });

  assert.equal(result.positions[0].productCode, "110022");
  assert.equal(result.positions[0].averageCost, "2.02501296");
  assert.deepEqual(result.summary, {
    totalCount: 1,
    totalMarketValue: "2700.00",
  });
  assert.equal(result.completeness.completeness, "COMPLETE");
});

test("持仓页跳转登录时拒绝把空页面当作空持仓", async () => {
  const page = new FakePage({
    finalUrl: "https://login.1234567.com.cn/",
    payload: null,
  });

  await assert.rejects(
    () => captureHoldings(page, { capturedAt: "2026-08-28T00:00:00+08:00", timeoutMs: 20 }),
    /登录已过期/,
  );
});

test("页面响应没有完整持仓结构时拒绝继续生成基线", async () => {
  const page = new FakePage({
    finalUrl: "https://trade.1234567.com.cn/myAssets/hold",
    payload: { code: 0, data: { news: [{ title: "非持仓数据" }] } },
  });

  await assert.rejects(
    () => captureHoldings(page, { capturedAt: "2026-08-28T00:00:00+08:00", timeoutMs: 20 }),
    /未捕获到结构化持仓数据/,
  );
});

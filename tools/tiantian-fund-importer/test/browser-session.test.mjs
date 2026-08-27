import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { openAuthenticatedSession } from "../src/browser-session.mjs";

test("登录确认后重新加载查询页并返回可关闭的认证会话", async () => {
  class FakePage extends EventEmitter {
    constructor() {
      super();
      this.navigationCount = 0;
    }

    async goto() {
      this.navigationCount += 1;
      if (this.navigationCount === 2) {
        queueMicrotask(() =>
          this.emit("request", {
            url: () => "https://query.1234567.com.cn/queryapi/trading/Query/index",
            headers: () => ({ "access-token": "session-token" }),
          }),
        );
      } else if (this.navigationCount === 3) {
        queueMicrotask(() =>
          this.emit("response", {
            url: () => "https://trade.1234567.com.cn/api/assets/holdings",
            headers: () => ({ "content-type": "application/json" }),
            json: async () => ({
              data: {
                totalCount: 1,
                totalMarketValue: "100.00",
                rows: [{
                  id: "holding-1",
                  fundCode: "110022",
                  fundName: "测试基金",
                  holdShare: "100.00",
                  averageCost: "1.00",
                  costAmount: "100.00",
                  marketValue: "100.00",
                }],
              },
            }),
          }),
        );
      }
    }

    async bringToFront() {}

    url() {
      return this.navigationCount >= 3
        ? "https://trade.1234567.com.cn/myAssets/hold"
        : "https://query.1234567.com.cn/";
    }
  }

  const page = new FakePage();
  const state = { closed: false, prompted: false };
  const context = {
    pages: () => [page],
    request: {
      post: async () => ({
        ok: () => true,
        json: async () => ({ code: 1200, data: { totalCount: 0, list: [] } }),
      }),
    },
    close: async () => {
      state.closed = true;
    },
  };
  const chromium = {
    launchPersistentContext: async () => context,
  };

  const session = await openAuthenticatedSession({
    chromium,
    profileDir: "ignored-in-test",
    browser: "msedge",
    prompt: async () => {
      state.prompted = true;
    },
    logger: () => {},
  });
  const response = await session.requestPage({
    windowStart: "2025-01-01",
    windowEnd: "2025-12-31",
    busType: "1",
    status: "2",
    isHistory: true,
    pageNum: 1,
    pageSize: 20,
  });

  assert.equal(state.prompted, true);
  assert.equal(page.navigationCount, 2);
  assert.equal(response.code, 1200);
  const holdings = await session.requestHoldings({
    capturedAt: "2026-08-28T00:00:00+08:00",
    timeoutMs: 100,
  });
  assert.equal(page.navigationCount, 3);
  assert.equal(holdings.positions[0].productCode, "110022");
  await session.close();
  assert.equal(state.closed, true);
});

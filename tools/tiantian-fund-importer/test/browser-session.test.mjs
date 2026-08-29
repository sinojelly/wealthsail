import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { openAuthenticatedSession } from "../src/browser-session.mjs";

test("交易查询和持仓子系统分别完成登录后返回可用会话", async () => {
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
      } else if (this.navigationCount === 4) {
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
      if (this.navigationCount === 3) return "https://login.1234567.com.cn/";
      if (this.navigationCount >= 4) {
        return "https://trade.1234567.com.cn/myAssets/hold";
      }
      return "https://query.1234567.com.cn/";
    }
  }

  const page = new FakePage();
  const state = { closed: false, promptCount: 0 };
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
      state.promptCount += 1;
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

  assert.equal(state.promptCount, 1);
  assert.equal(page.navigationCount, 2);
  assert.equal(response.code, 1200);
  const holdings = await session.requestHoldings({
    capturedAt: "2026-08-28T00:00:00+08:00",
    timeoutMs: 100,
  });
  assert.equal(state.promptCount, 2);
  assert.equal(page.navigationCount, 4);
  assert.equal(holdings.positions[0].productCode, "110022");
  await session.close();
  assert.equal(state.closed, true);
});

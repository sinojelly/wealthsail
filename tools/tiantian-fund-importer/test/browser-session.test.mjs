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
      }
    }

    async bringToFront() {}
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
  await session.close();
  assert.equal(state.closed, true);
});

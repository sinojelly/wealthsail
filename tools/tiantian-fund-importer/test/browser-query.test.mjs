import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { createApiRequester, waitForQueryAccessToken } from "../src/browser-query.mjs";

test("浏览器只把内存中的访问令牌放入查询请求头", async () => {
  let capturedOptions;
  const apiContext = {
    async post(url, options) {
      capturedOptions = { url, options };
      return {
        ok: () => true,
        json: async () => ({ code: 1200, data: { totalCount: 0, list: [] } }),
      };
    },
  };
  const requestPage = createApiRequester({ apiContext, accessToken: "memory-only-token" });

  const result = await requestPage({
    windowStart: "2025-01-01",
    windowEnd: "2025-12-31",
    busType: "1",
    status: "2",
    isHistory: true,
    pageNum: 1,
    pageSize: 20,
  });

  assert.equal(result.code, 1200);
  assert.equal(capturedOptions.url, "https://query.1234567.com.cn/queryapi/trading/Query/DelegateList");
  assert.equal(capturedOptions.options.headers["ACCESS-TOKEN"], "memory-only-token");
  assert.equal(JSON.stringify(capturedOptions.options.data).includes("memory-only-token"), false);
});

test("等待令牌时忽略其他站点请求并在获取后解除监听", async () => {
  class FakePage extends EventEmitter {}
  const page = new FakePage();
  const tokenPromise = waitForQueryAccessToken(page, { timeoutMs: 1000 });

  page.emit("request", {
    url: () => "https://example.invalid/queryapi/trading/Query/index",
    headers: () => ({ "access-token": "wrong" }),
  });
  page.emit("request", {
    url: () => "https://query.1234567.com.cn/queryapi/trading/Query/index",
    headers: () => ({ "access-token": "right" }),
  });

  assert.equal(await tokenPromise, "right");
  assert.equal(page.listenerCount("request"), 0);
});

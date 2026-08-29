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

class OfficialHoldingPage extends EventEmitter {
  constructor({ secondMarketValue = "1093.02" } = {}) {
    super();
    this.currentUrl = "https://trade.1234567.com.cn/myAssets/hold";
    this.navigations = [];
    this.secondMarketValue = secondMarketValue;
  }

  async goto(url) {
    this.currentUrl = url;
    this.navigations.push(url);
    if (url.includes("/myAssets/hold")) {
      queueMicrotask(() =>
        this.emit("response", {
          url: () => "https://trade.1234567.com.cn/request/hold",
          headers: () => ({ "content-type": "application/json; charset=utf-8" }),
          json: async () => ({
            succeed: true,
            result: {
              assetTotal: [{ strValue: "2,700.00" }],
              assetList: [
                {
                  fundCode: "110022",
                  fundName: "易方达消费行业股票",
                  assetValue: "2700.00",
                  profitValue: "200.00",
                  profitPercent: "8.00",
                  nav: "2.1876",
                  navdate: "2026-08-27",
                  isHQB: false,
                  isVirtual: false,
                },
              ],
            },
          }),
        }),
      );
    }
  }

  url() {
    return this.currentUrl;
  }

  async waitForFunction() {}

  async evaluate() {
    return {
      headers: ["关联银行卡", "可用份额(份)", "持仓份额(份)", "持仓市值(元)", "操作"],
      rows: [
        ["中信银行|6222000012345678", "700.00", "734.56", "1606.97", "卖出转换"],
        [
          "建设银行|6227000087654321",
          "500.00",
          "500.00",
          this.secondMarketValue,
          "卖出转换",
        ],
      ],
    };
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

test("银行卡明细与主持仓差额超过一分时拒绝生成基线", async () => {
  const page = new OfficialHoldingPage({ secondMarketValue: "1093.00" });

  await assert.rejects(
    () =>
      captureHoldings(page, {
        capturedAt: "2026-08-28T00:00:00+08:00",
        timeoutMs: 100,
      }),
    /绝对差额 0\.03 元/,
  );
});

test("按天天基金官方持仓结构逐基金汇总多银行卡明细", async () => {
  const page = new OfficialHoldingPage();

  const result = await captureHoldings(page, {
    capturedAt: "2026-08-28T00:00:00+08:00",
    timeoutMs: 100,
  });

  assert.equal(page.navigations.length, 2);
  assert.match(page.navigations[1], /\/myassets\/single\?.*fc=110022/);
  assert.deepEqual(result.positions[0], {
    sourceProvider: "TIANTIAN_FUND",
    sourceRecordId: "110022",
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
    capturedAt: "2026-08-28T00:00:00+08:00",
  });
  assert.deepEqual(result.summary, { totalCount: 1, totalMarketValue: "2,700.00" });
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

import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { runCapture } from "../src/run.mjs";

test("一次采集同时输出当前持仓、本地交易报告和可上传 v2 ZIP", async () => {
  const privateRoot = await mkdtemp(path.join(tmpdir(), "wealthsail-capture-run-"));
  const session = {
    requestHoldings: async () => ({
      positions: [{
        sourceProvider: "TIANTIAN_FUND",
        sourceRecordId: "holding-110022",
        productCode: "110022",
        productName: "测试基金",
        quantity: "100.00",
        availableQuantity: "100.00",
        averageCost: "1.00",
        costAmount: "100.00",
        marketValue: "110.00",
        holdingProfit: "10.00",
        holdingProfitRate: "10.00",
        latestNav: "1.10",
        latestNavDate: "2026-08-27",
        accountMask: "****5678",
        capturedAt: "2026-08-28T00:00:00.000Z",
      }],
      completeness: { completeness: "COMPLETE", reasons: [], positionCount: 1 },
    }),
    requestPage: async () => ({
      code: 1200,
      data: {
        totalCount: 1,
        list: [{
          id: "tx-1",
          strikeStartDate: "2026-08-20",
          productCode: "110022",
          productShowName: "测试基金",
          businessTypeText1: "买入",
          applyCount: "100.00",
          applyCountUnit: "元",
          confirmCount: "90.00",
          confirmCountUnit: "份",
          appStateText: "成功",
        }],
      },
    }),
  };

  try {
    const result = await runCapture({
      options: {
        from: "2026-01-01",
        to: "2026-08-28",
        outputDir: path.join(privateRoot, "exports"),
        profileDir: path.join(privateRoot, "browser-profile"),
      },
      plans: [{
        windowStart: "2026-01-01",
        windowEnd: "2026-08-28",
        businessTypeLabel: "全部",
      }],
      session,
      delay: async () => {},
      now: new Date("2026-08-28T00:00:00.000Z"),
    });

    assert.equal(result.manifest.counts.currentPositions, 1);
    assert.equal(result.manifest.counts.fundTransactions, 1);
    assert.equal(result.manifest.collectorVersion, "0.2.0");
    assert.match(result.zipPath, /wealthsail-tiantian-20260828\.zip$/);

    const localPositions = JSON.parse(
      await readFile(path.join(result.runDir, "current-positions.json"), "utf8"),
    );
    assert.equal(localPositions[0].productCode, "110022");
    const localManifest = JSON.parse(
      await readFile(path.join(result.runDir, "manifest.json"), "utf8"),
    );
    assert.equal(localManifest.schemaVersion, "wealthsail-tiantian-import/v1");
  } finally {
    await rm(privateRoot, { recursive: true, force: true });
  }
});

import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import { writeRunOutput } from "../src/output.mjs";

test("输出按分类分文件并且清单不包含会话秘密", async () => {
  const outputRoot = await mkdtemp(path.join(tmpdir(), "wealthsail-tiantian-output-"));
  try {
    const records = [
      {
        sourceId: "fund-1",
        productCode: "001632",
        productName: "某基金",
        businessType: "买入",
        status: "成功",
        category: "FUND_TRADE",
        accountMask: "中信银行 | ****8671",
      },
      {
        sourceId: "cash-1",
        productCode: "",
        productName: "活期宝",
        businessType: "活期宝每日分红",
        status: "成功",
        category: "CASH_MANAGEMENT",
        accountMask: "",
      },
      {
        sourceId: "review-1",
        productCode: "",
        productName: "未知产品",
        businessType: "其他",
        status: "成功",
        category: "REVIEW_REQUIRED",
        accountMask: "",
      },
    ];

    const result = await writeRunOutput({
      outputRoot,
      runId: "test-run",
      records,
      duplicateCount: 2,
      metadata: { from: "2020-01-01", to: "2026-08-27", accessToken: "never-write-me" },
    });

    const manifestText = await readFile(path.join(result.runDir, "manifest.json"), "utf8");
    const manifest = JSON.parse(manifestText);
    assert.deepEqual(manifest.counts, {
      total: 3,
      fundTrades: 1,
      cashManagementExcluded: 1,
      reviewRequired: 1,
      nonPosting: 0,
      duplicatesRemoved: 2,
    });
    assert.equal(manifestText.includes("never-write-me"), false);

    const fundCsv = await readFile(path.join(result.runDir, "fund-transactions.csv"), "utf8");
    const excludedCsv = await readFile(path.join(result.runDir, "excluded-cash-management.csv"), "utf8");
    assert.match(fundCsv, /001632/);
    assert.doesNotMatch(fundCsv, /活期宝每日分红/);
    assert.match(excludedCsv, /活期宝每日分红/);
  } finally {
    await rm(outputRoot, { recursive: true, force: true });
  }
});

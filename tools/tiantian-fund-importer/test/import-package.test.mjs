import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  buildImportPackage,
  loadOrCreateExternalAccountKey,
} from "../src/import-package.mjs";

const position = {
  sourceProvider: "TIANTIAN_FUND",
  sourceRecordId: "holding-110022",
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
};

test("生成的 v2 ZIP 只包含清单、持仓、基金流水、待复核和排除摘要", async () => {
  const outputDir = await mkdtemp(path.join(tmpdir(), "wealthsail-package-"));
  try {
    const result = await buildImportPackage({
      outputDir,
      packageDate: "2026-08-28",
      collectorVersion: "0.2.0",
      externalAccountKey: "1f8a8407-9d91-4a1d-b954-9ad46fbd9585",
      sourceWindowStart: "2020-01-01",
      sourceWindowEnd: "2026-08-28",
      positionsAsOf: "2026-08-28T00:00:00+08:00",
      completeness: "COMPLETE",
      positions: [position],
      transactions: [{ sourceId: "tx-1", productCode: "110022" }],
      reviewRequired: [{ sourceId: "tx-review" }],
      excludedSummary: { cashManagement: 1545, nonPosting: 4 },
    });

    assert.equal(result.manifest.schemaVersion, "wealthsail-tiantian-import/v2");
    assert.deepEqual(
      result.manifest.files.map((file) => file.name).sort(),
      [
        "current-positions.json",
        "excluded-summary.json",
        "fund-transactions.json",
        "review-required.json",
      ],
    );
    assert.deepEqual(result.manifest.counts, {
      currentPositions: 1,
      fundTransactions: 1,
      reviewRequired: 1,
      cashManagementExcluded: 1545,
      nonPostingExcluded: 4,
    });
    assert.match(result.zipPath, /wealthsail-tiantian-20260828\.zip$/);

    const zipBytes = await readFile(result.zipPath);
    assert.equal(zipBytes.subarray(0, 2).toString("ascii"), "PK");
    const binaryText = zipBytes.toString("latin1");
    for (const name of [
      "manifest.json",
      "current-positions.json",
      "fund-transactions.json",
      "review-required.json",
      "excluded-summary.json",
    ]) {
      assert.equal(binaryText.includes(name), true, `${name} 应进入 ZIP`);
    }
    assert.equal(binaryText.includes("excluded-cash-management.csv"), false);
  } finally {
    await rm(outputDir, { recursive: true, force: true });
  }
});

test("匿名来源账户不是 UUID 时拒绝生成可上传包", async () => {
  const outputDir = await mkdtemp(path.join(tmpdir(), "wealthsail-package-"));
  try {
    await assert.rejects(
      () =>
        buildImportPackage({
          outputDir,
          packageDate: "2026-08-28",
          collectorVersion: "0.2.0",
          externalAccountKey: "13800138000",
          sourceWindowStart: "2020-01-01",
          sourceWindowEnd: "2026-08-28",
          positionsAsOf: "2026-08-28T00:00:00+08:00",
          completeness: "COMPLETE",
          positions: [position],
          transactions: [],
          reviewRequired: [],
          excludedSummary: { cashManagement: 0, nonPosting: 0 },
        }),
      /匿名来源账户标识必须是 UUID/,
    );
  } finally {
    await rm(outputDir, { recursive: true, force: true });
  }
});

test("匿名来源账户标识在同一本机私有目录中保持稳定", async () => {
  const privateDir = await mkdtemp(path.join(tmpdir(), "wealthsail-account-key-"));
  try {
    const first = await loadOrCreateExternalAccountKey(privateDir);
    const second = await loadOrCreateExternalAccountKey(privateDir);
    assert.match(first, /^[0-9a-f-]{36}$/);
    assert.equal(second, first);

    const stored = await readFile(path.join(privateDir, "source-account.json"), "utf8");
    assert.equal(stored.includes("手机号"), false);
    assert.equal(stored.includes("银行卡"), false);
    assert.deepEqual(Object.keys(JSON.parse(stored)).sort(), ["externalAccountKey", "version"]);
  } finally {
    await rm(privateDir, { recursive: true, force: true });
  }
});

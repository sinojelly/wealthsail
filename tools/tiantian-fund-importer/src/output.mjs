import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { toCsv } from "./csv.mjs";

const CSV_COLUMNS = [
  "sourceProvider",
  "sourceId",
  "traceNo",
  "occurredAt",
  "productCode",
  "productName",
  "businessCode",
  "businessType",
  "applicationValue",
  "applicationUnit",
  "confirmationValue",
  "confirmationUnit",
  "accountMask",
  "status",
  "category",
  "classificationReason",
  "detailRef",
  "sourceWindowStart",
  "sourceWindowEnd",
  "sourcePage",
];

function sha256(content) {
  return createHash("sha256").update(content).digest("hex");
}

function safeRunId(runId) {
  if (!/^[A-Za-z0-9._-]+$/.test(runId)) {
    throw new Error("运行标识只能包含字母、数字、点、下划线和连字符");
  }
  return runId;
}

function jsonContent(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

export async function writeRunOutput({
  outputRoot,
  runId,
  records,
  duplicateCount,
  metadata = {},
}) {
  const runDir = path.join(path.resolve(outputRoot), safeRunId(runId));
  await mkdir(runDir, { recursive: false });

  const fundTrades = records.filter((record) => record.category === "FUND_TRADE");
  const cashManagement = records.filter((record) => record.category === "CASH_MANAGEMENT");
  const reviewRequired = records.filter((record) => record.category === "REVIEW_REQUIRED");
  const nonPosting = records.filter((record) => record.category === "NON_POSTING");

  const contents = {
    "all-transactions.json": jsonContent(records),
    "fund-transactions.csv": toCsv(fundTrades, CSV_COLUMNS),
    "excluded-cash-management.csv": toCsv(cashManagement, CSV_COLUMNS),
    "review-required.csv": toCsv(reviewRequired, CSV_COLUMNS),
    "non-posting.csv": toCsv(nonPosting, CSV_COLUMNS),
  };

  const manifest = {
    schemaVersion: "wealthsail-tiantian-import/v1",
    provider: "TIANTIAN_FUND",
    generatedAt: new Date().toISOString(),
    range: {
      from: metadata.from || "",
      to: metadata.to || "",
    },
    counts: {
      total: records.length,
      fundTrades: fundTrades.length,
      cashManagementExcluded: cashManagement.length,
      reviewRequired: reviewRequired.length,
      nonPosting: nonPosting.length,
      duplicatesRemoved: duplicateCount,
    },
    files: Object.fromEntries(
      Object.entries(contents).map(([name, content]) => [name, { sha256: sha256(content) }]),
    ),
    security: {
      credentialsStored: false,
      accessTokenStored: false,
      accountNumbersMasked: true,
    },
  };
  contents["manifest.json"] = jsonContent(manifest);

  await Promise.all(
    Object.entries(contents).map(([name, content]) =>
      writeFile(path.join(runDir, name), content, { encoding: "utf8", flag: "wx" }),
    ),
  );

  return { runDir, manifest };
}

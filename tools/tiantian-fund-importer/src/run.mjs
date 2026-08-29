import path from "node:path";
import { mkdir } from "node:fs/promises";

import { collectTransactions } from "./collector.mjs";
import { deduplicateTransactions } from "./domain.mjs";
import {
  buildImportPackage,
  loadOrCreateExternalAccountKey,
} from "./import-package.mjs";
import { writeRunOutput } from "./output.mjs";

export const COLLECTOR_VERSION = "0.3.0";

function runId(now) {
  return `run-${now.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z")}`;
}

export async function runCapture({
  options,
  plans,
  session,
  delay,
  onProgress = () => {},
  now = new Date(),
}) {
  await mkdir(options.outputDir, { recursive: true });
  const capturedAt = now.toISOString();
  const holdings = await session.requestHoldings({ capturedAt, detailDelay: delay });
  const collected = await collectTransactions({
    plans,
    requestPage: session.requestPage,
    delay,
    onProgress,
  });
  const deduplicated = deduplicateTransactions(collected);
  const local = await writeRunOutput({
    outputRoot: options.outputDir,
    runId: runId(now),
    records: deduplicated.records,
    positions: holdings.positions,
    duplicateCount: deduplicated.duplicateCount,
    metadata: { from: options.from, to: options.to },
  });

  const fundTrades = deduplicated.records.filter(
    (record) => record.category === "FUND_TRADE",
  );
  const reviewRequired = deduplicated.records.filter(
    (record) => record.category === "REVIEW_REQUIRED",
  );
  const externalAccountKey = await loadOrCreateExternalAccountKey(
    path.dirname(options.profileDir),
  );
  const upload = await buildImportPackage({
    outputDir: local.runDir,
    packageDate: options.to,
    collectorVersion: COLLECTOR_VERSION,
    externalAccountKey,
    sourceWindowStart: options.from,
    sourceWindowEnd: options.to,
    positionsAsOf: capturedAt,
    completeness: holdings.completeness.completeness,
    positions: holdings.positions,
    transactions: fundTrades,
    reviewRequired,
    excludedSummary: {
      cashManagement: deduplicated.records.filter(
        (record) => record.category === "CASH_MANAGEMENT",
      ).length,
      nonPosting: deduplicated.records.filter(
        (record) => record.category === "NON_POSTING",
      ).length,
      duplicateCount: deduplicated.duplicateCount,
      completenessReasons: holdings.completeness.reasons,
    },
  });

  return {
    runDir: local.runDir,
    zipPath: upload.zipPath,
    manifest: upload.manifest,
    localManifest: local.manifest,
  };
}

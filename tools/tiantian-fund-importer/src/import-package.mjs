import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { strToU8, zipSync } from "fflate";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function loadOrCreateExternalAccountKey(privateDir) {
  const configPath = path.join(path.resolve(privateDir), "source-account.json");
  await mkdir(path.dirname(configPath), { recursive: true });

  async function readExisting() {
    const parsed = JSON.parse(await readFile(configPath, "utf8"));
    if (parsed.version !== 1 || !UUID.test(parsed.externalAccountKey || "")) {
      throw new Error("本地匿名来源账户配置损坏，请先备份后删除并重新生成");
    }
    return parsed.externalAccountKey;
  }

  try {
    return await readExisting();
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }

  const externalAccountKey = randomUUID();
  try {
    await writeFile(
      configPath,
      `${JSON.stringify({ version: 1, externalAccountKey }, null, 2)}\n`,
      { encoding: "utf8", flag: "wx" },
    );
    return externalAccountKey;
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
    return readExisting();
  }
}

function jsonBytes(value) {
  return strToU8(`${JSON.stringify(value, null, 2)}\n`);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function dateToken(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new Error("数据包日期必须是 YYYY-MM-DD");
  }
  return value.replaceAll("-", "");
}

export async function buildImportPackage(input) {
  if (!UUID.test(input.externalAccountKey || "")) {
    throw new Error("匿名来源账户标识必须是 UUID");
  }
  await mkdir(input.outputDir, { recursive: true });

  const payloads = {
    "current-positions.json": input.positions,
    "fund-transactions.json": input.transactions,
    "review-required.json": input.reviewRequired,
    "excluded-summary.json": input.excludedSummary,
  };
  const contents = Object.fromEntries(
    Object.entries(payloads).map(([name, payload]) => [name, jsonBytes(payload)]),
  );
  const recordCount = (payload) => (Array.isArray(payload) ? payload.length : 1);
  const generatedAt = new Date().toISOString();
  const manifest = {
    schemaVersion: "wealthsail-tiantian-import/v2",
    collectorVersion: input.collectorVersion,
    provider: "TIANTIAN_FUND",
    externalAccountKey: input.externalAccountKey,
    sourceWindowStart: input.sourceWindowStart,
    sourceWindowEnd: input.sourceWindowEnd,
    positionsAsOf: input.positionsAsOf,
    generatedAt,
    completeness: input.completeness,
    counts: {
      currentPositions: input.positions.length,
      fundTransactions: input.transactions.length,
      reviewRequired: input.reviewRequired.length,
      cashManagementExcluded: Number(input.excludedSummary.cashManagement || 0),
      nonPostingExcluded: Number(input.excludedSummary.nonPosting || 0),
    },
    files: Object.entries(contents).map(([name, bytes]) => ({
      name,
      sha256: sha256(bytes),
      records: recordCount(payloads[name]),
    })),
    security: {
      credentialsStored: false,
      accessTokenStored: false,
      accountNumbersMasked: true,
    },
  };

  const archive = zipSync(
    {
      "manifest.json": jsonBytes(manifest),
      ...contents,
    },
    { level: 6 },
  );
  const zipPath = path.join(
    path.resolve(input.outputDir),
    `wealthsail-tiantian-${dateToken(input.packageDate)}.zip`,
  );
  await writeFile(zipPath, archive, { flag: "wx" });
  return { zipPath, manifest };
}

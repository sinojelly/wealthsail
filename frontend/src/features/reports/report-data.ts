import type { QualityCode } from "../../api/types";

export interface EvidenceRecord {
  id: string;
  type: string;
  title: string;
  content: string;
  source: string;
  sourceUrl: string;
  asOf: string;
  publishedAt: string;
  retrievedAt: string;
  retrievalMethod: string;
  schemaVersion: string;
  modelRunId?: string;
  quality: QualityCode;
  hash: string;
}

import type { QualityCode } from "../../api/types";

export interface EvidenceRecord {
  id: string;
  type: string;
  title: string;
  content: string;
  source: string;
  sourceUrl?: string;
  asOf: string;
  claimType: string;
  sourceSnapshotId?: string;
  quality: QualityCode;
  hash?: string;
}

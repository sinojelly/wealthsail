// 与后端 /api/v1 统一信封契约对齐的 DTO 类型。
// 金额一律为十进制字符串（DecimalString），严禁在前端账本路径转为 number。

export type DecimalString = string;
export type QualityCode = "FRESH" | "ESTIMATED" | "STALE" | "MISSING";
export type ReportSession = "PRE_MARKET" | "INTRADAY" | "FUND_1430" | "POST_MARKET";
export type TenantRole = "OWNER" | "ADMIN" | "EDITOR" | "VIEWER";
export type InvitableTenantRole = Exclude<TenantRole, "OWNER">;

// ---------------------------------------------------------------------------
// 统一信封
// ---------------------------------------------------------------------------
export interface ApiMeta {
  requestId: string;
  asOf: string;
  qualityStatus: QualityCode;
  schemaVersion: number;
}

export interface ApiEnvelope<T> {
  data: T;
  meta: ApiMeta;
}

export interface ApiFieldError {
  field: string;
  message: string;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    fieldErrors: ApiFieldError[];
  };
  requestId: string;
}

// ---------------------------------------------------------------------------
// portfolio
// ---------------------------------------------------------------------------
export interface AllocationItem {
  category: string;
  amount: DecimalString;
  value: DecimalString | null; // 百分比；总资产不完整时不可计算
}

export interface RiskPreviewDto {
  draftId: string;
  expectedVersion: number;
  previousMaxPosition: DecimalString | null;
  nextMaxPosition: DecimalString;
  impactedHoldings: number;
  newAlerts: number;
  policyHash: string;
  status: "PREVIEW";
}

export interface RiskConfirmDto {
  id: string;
  version: number;
  status: "CONFIRMED";
  policyHash: string;
}

export interface PortfolioSummary {
  totalAsset: DecimalString | null;
  knownValue: DecimalString;
  valuationCoveragePct: DecimalString;
  unpricedPositionCount: number;
  currency: string;
  cashBalance: DecimalString;
  positionsMarketValue: DecimalString | null;
  dailyPnl: DecimalString | null;
  dailyPnlRate: DecimalString | null;
  cumulativePnl: DecimalString | null;
  cumulativePnlRate: DecimalString | null;
  allocation: AllocationItem[];
  qualityStatus: QualityCode;
  asOf: string;
  positionCount: number;
}

export interface PositionDto {
  symbol: string;
  name: string;
  type: string; // A股 / 场内ETF / 场外基金 / 现金 …
  quantity: string;
  avgCost: DecimalString;
  price: DecimalString | null;
  marketValue: DecimalString | null;
  weight: DecimalString | null;
  pnl: DecimalString | null;
  pnlRate: DecimalString | null;
  quality: QualityCode;
  accountId: string;
}

export type InstrumentType = "STOCK" | "ETF" | "FUND";

export interface InstrumentSearchResult {
  symbol: string;
  name: string;
  market: string;
  type: InstrumentType;
  source?: "local" | "live";
}

export interface PositionMutationPayload {
  symbol: string;
  name?: string;
  assetType: InstrumentType;
  quantity: string;
  avgCost: string;
  buyDate?: string;
  idempotencyKey?: string;
}

export interface PositionUpdatePayload {
  quantity: string;
  avgCost: string;
  buyDate?: string;
  idempotencyKey?: string;
}

export interface PositionMutationResult {
  position: PositionDto & {
    avgCost: DecimalString;
    buyDate: string;
    eventId: string;
  };
}

export interface DeletePositionResult {
  removed: { symbol: string; name: string; quantity: string };
}

export interface NavPoint {
  date: string;
  totalNav: DecimalString | null;
  dailyPnl: DecimalString | null;
  cashBalance: DecimalString;
}

export interface NavTrend {
  points: NavPoint[];
  benchmark: null;
  currency: string;
  timezone: string;
}

// ---------------------------------------------------------------------------
// reports
// ---------------------------------------------------------------------------
export interface ReportSummary {
  id: string;
  session: ReportSession;
  title: string;
  subtitle: string;
  tradeDate: string;
  version: number;
  generatedAt: string;
  status: string;
  qualityStatus: QualityCode;
}

export interface ReportItem {
  id: string;
  itemType: "FACT" | "RISK" | "ACTION_PLAN" | "TODO" | "FUND_ESTIMATE" | "ALERT";
  severity: "INFO" | "WARNING" | "CRITICAL";
  title: string;
  body: string;
  actionPlan: unknown;
  quality: QualityCode;
  evidence: ReportEvidence[];
}

export interface ReportEvidence {
  id: string;
  claimType: "FACT" | "INFERENCE" | "OPINION";
  sourceSnapshotId: string | null;
  sourceName: string | null;
  originUri: string | null;
  asOf: string;
  excerpt: string | null;
  quality: QualityCode;
  contentHash: string | null;
}

export interface ReportVersionRef {
  id: string;
  version: number;
  status: string;
  asOf: string;
  supersedesReportId: string | null;
}

export interface ModelRunInfo {
  provider: string;
  model: string;
  status: string;
  tokenUsage: { prompt_tokens?: number; completion_tokens?: number };
  costCny: DecimalString;
  startedAt: string;
  finishedAt: string;
  errorClass: string | null;
}

export interface ReportDetail {
  id: string;
  session: ReportSession;
  title: string;
  subtitle: string;
  tradeDate: string;
  version: number;
  status: string;
  asOf: string;
  qualityStatus: QualityCode;
  summary: string;
  policyCheckStatus: string;
  validUntil: string;
  supersedesReportId: string | null;
  modelRun: ModelRunInfo | null;
  items: ReportItem[];
  versionHistory: ReportVersionRef[];
}

// ---------------------------------------------------------------------------
// risk
// ---------------------------------------------------------------------------
export interface RiskPolicy {
  id: string;
  version: number;
  status: "DRAFT" | "CONFIRMED" | "RETIRED";
  effectiveFrom: string;
  effectiveTo: string | null;
  confirmedAt: string | null;
  confirmedBy: string | null;
  confirmationProvenance: string | null;
  policyHash: string;
  accountId: string | null;
}

export interface RiskBudget {
  maxDrawdownLimit: DecimalString | null;
  singlePositionLimit: DecimalString | null;
  equityTargetPct: DecimalString | null;
  bondTargetPct: DecimalString | null;
  goldTargetPct: DecimalString | null;
  cashTargetPct: DecimalString | null;
  rebalanceTolerancePct: DecimalString | null;
  minCashRatio: DecimalString | null;
  investmentHorizonMonths: number | null;
  cashNeedAmount: DecimalString | null;
  allowedAssetTypes: string[] | null;
  alertBudgetPerDay: number | null;
}

export interface PoliciesResponse {
  current: RiskPolicy | null;
  history: RiskPolicy[];
  budget: RiskBudget;
}

export interface RiskRule {
  id: string;
  ruleType: string;
  name: string;
  params: Record<string, unknown>;
  silenceWindowMinutes: number;
  status: string;
  instrumentName: string | null;
  lastTriggeredAt: string | null;
}

export interface RiskCheck {
  check: string;
  name: string;
  limit: DecimalString;
  current: DecimalString;
  status: "OK" | "WARNING" | "VIOLATION";
  message: string;
}

export interface RulesResponse {
  rules: RiskRule[];
  checks: RiskCheck[];
}

// ---------------------------------------------------------------------------
// identity
// ---------------------------------------------------------------------------
export interface LoginUser {
  id: string;
  email: string;
  nickname: string;
  tenantId: string;
  tenantName: string;
  role: TenantRole;
}

export interface LoginResponse {
  accessToken: string;
  tokenType: string;
  expiresIn: number;
  user: LoginUser;
}

export interface BindingCredential {
  token: string;
  expiresInSeconds: number;
  channelType: string;
}

export interface InvitationCredential {
  invitationId: string;
  token: string;
  email: string;
  role: InvitableTenantRole;
  expiresAt: string;
}

export interface MeProfile {
  id: string;
  email: string | null;
  nickname: string;
  avatarUrl: string | null;
  createdAt: string;
}

export interface MeTenant {
  tenantId: string;
  name: string;
  role: TenantRole;
  status: string;
}

export interface MeBinding {
  id: string;
  channelType: string;
  externalUsername: string | null;
  status: string;
  boundAt: string | null;
  lastUsedAt: string | null;
}

export interface MeResponse {
  profile: MeProfile;
  activeTenantId: string;
  activeRole: TenantRole;
  tenants: MeTenant[];
  bindings: MeBinding[];
}

// ---------------------------------------------------------------------------
// operations
// ---------------------------------------------------------------------------
export interface OpsJob {
  id: string;
  jobName: string;
  cronExpr: string;
  reportSession: ReportSession | null;
  isEnabled: boolean;
}

export interface OpsJobRun {
  id: string;
  jobId: string | null;
  jobKey: string;
  startedAt: string;
  finishedAt: string | null;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "ABSTAINED";
  errorClass: string | null;
  durationSeconds: number | null;
}

export interface OpsJobsResponse {
  jobs: OpsJob[];
  runs: OpsJobRun[];
}

export interface FreshnessInfo {
  latestAsOf: string | null;
  freshCount: number;
  total: number;
}

export interface FreshnessResponse {
  nav: FreshnessInfo;
  positions: FreshnessInfo;
}

export interface OutboxMessage {
  id: string;
  topic: string;
  aggregateType: string;
  aggregateId: string;
  availableAt: string;
  status: string;
  attemptCount: number;
  sentAt: string | null;
  createdAt: string;
}

export interface OutboxResponse {
  counts: { pending: number; sending: number; sent: number; dead: number };
  recent: OutboxMessage[];
}

export interface AuditRow {
  id: string;
  actorType: string;
  actorId: string;
  action: string;
  resourceType: string;
  resourceId: string;
  correlationId: string | null;
  createdAt: string;
}

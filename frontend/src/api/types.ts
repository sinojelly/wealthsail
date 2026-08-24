// 与后端 /api/v1 统一信封契约对齐的 DTO 类型。
// 金额一律为十进制字符串（DecimalString），严禁在前端账本路径转为 number。

export type DecimalString = string;
export type QualityCode = "FRESH" | "ESTIMATED" | "STALE" | "MISSING";
export type ReportSession = "PRE_MARKET" | "INTRADAY" | "FUND_1430" | "POST_MARKET";
export type TenantRole = "OWNER" | "ADMIN" | "EDITOR" | "VIEWER";

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
  value: DecimalString; // 百分比
}

export interface SettlementPreviewDto {
  fillId: string;
  cashBefore: DecimalString;
  cashDelta: DecimalString;
  cashAfter: DecimalString;
}

export interface RiskPreviewDto {
  previousMaxPosition: DecimalString;
  nextMaxPosition: DecimalString;
  impactedHoldings: number;
  newAlerts: number;
  policyHash: string;
}

export interface PortfolioSummary {
  totalAsset: DecimalString;
  currency: string;
  cashBalance: DecimalString;
  positionsMarketValue: DecimalString;
  dailyPnl: DecimalString;
  dailyPnlRate: DecimalString;
  cumulativePnl: DecimalString;
  cumulativePnlRate: DecimalString;
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
  price: DecimalString | null;
  marketValue: DecimalString;
  weight: DecimalString;
  pnl: DecimalString;
  pnlRate: DecimalString;
  quality: QualityCode;
  accountId: string;
}

export interface NavPoint {
  date: string;
  totalNav: DecimalString;
  dailyPnl: DecimalString;
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

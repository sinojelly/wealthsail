// 测试专用 fixture：与后端 /api/v1 响应形状一致的确定性数据。
import type {
  AuditRow,
  FreshnessResponse,
  MeResponse,
  NavTrend,
  OpsJobsResponse,
  OutboxResponse,
  PoliciesResponse,
  PortfolioSummary,
  PositionDto,
  ReportDetail,
  ReportSummary,
  RulesResponse,
} from "../api/types";

export const summaryFixture: PortfolioSummary = {
  totalAsset: "1921840.00",
  knownValue: "1921840.00",
  valuationCoveragePct: "100.00",
  unpricedPositionCount: 0,
  currency: "CNY",
  cashBalance: "201382.00",
  positionsMarketValue: "1720458.00",
  dailyPnl: "12384.00",
  dailyPnlRate: "0.65",
  cumulativePnl: "152800.00",
  cumulativePnlRate: "19.40",
  allocation: [
    { category: "A股", amount: "928000.00", value: "48.3" },
    { category: "场内ETF", amount: "359384.00", value: "18.7" },
    { category: "场外基金", amount: "432504.00", value: "22.5" },
    { category: "现金", amount: "201382.00", value: "10.5" },
  ],
  qualityStatus: "FRESH",
  asOf: "2026-08-23T16:00:00+08:00",
  positionCount: 6,
};

export const positionsFixture: PositionDto[] = [
  {
    symbol: "600036",
    name: "招商银行",
    type: "A股",
    quantity: "8500",
    price: "38.82",
    marketValue: "329970.00",
    weight: "17.17",
    pnl: "28472.00",
    pnlRate: "9.56",
    quality: "FRESH",
    accountId: "acc-1",
  },
  {
    symbol: "510300",
    name: "沪深300ETF",
    type: "场内ETF",
    quantity: "62000",
    price: "4.140",
    marketValue: "256680.00",
    weight: "13.36",
    pnl: "21806.00",
    pnlRate: "9.28",
    quality: "FRESH",
    accountId: "acc-1",
  },
];

export const navTrendFixture: NavTrend = {
  points: [
    { date: "2026-07-14", totalNav: "1768000.00", dailyPnl: "8000.00", cashBalance: "201382.00" },
    { date: "2026-07-15", totalNav: "1776000.00", dailyPnl: "12384.00", cashBalance: "201382.00" },
    { date: "2026-08-23", totalNav: "1921840.00", dailyPnl: "12384.00", cashBalance: "201382.00" },
  ],
  benchmark: null,
  currency: "CNY",
  timezone: "Asia/Shanghai",
};

export const reportsFixture: ReportSummary[] = [
  {
    id: "R-PRE-20260823",
    session: "PRE_MARKET",
    title: "盘前简报",
    subtitle: "隔夜市场、今日事件与持仓风险",
    tradeDate: "2026-08-23",
    version: 1,
    generatedAt: "2026-08-23T08:10:00+08:00",
    status: "已发布",
    qualityStatus: "FRESH",
  },
  {
    id: "R-POST-20260823",
    session: "POST_MARKET",
    title: "盘后交互复盘",
    subtitle: "计划—决策—执行闭环与证据回看",
    tradeDate: "2026-08-23",
    version: 1,
    generatedAt: "2026-08-23T16:35:00+08:00",
    status: "已发布",
    qualityStatus: "FRESH",
  },
];

export const reportDetailFixture: ReportDetail = {
  id: "R-POST-20260823",
  session: "POST_MARKET",
  title: "盘后交互复盘",
  subtitle: "计划—决策—执行闭环与证据回看",
  tradeDate: "2026-08-23",
  version: 2,
  status: "PUBLISHED",
  asOf: "2026-08-23T16:35:00+08:00",
  qualityStatus: "FRESH",
  summary: "盘后复盘：计划与动作核对完成，当日收益 +12,384 元。",
  policyCheckStatus: "PASSED",
  validUntil: "2026-08-23T23:59:59+08:00",
  supersedesReportId: "R-POST-20260823-v1",
  modelRun: {
    provider: "deepseek",
    model: "deepseek-chat",
    status: "COMPLETED",
    tokenUsage: { prompt_tokens: 980, completion_tokens: 720 },
    costCny: "0.0262",
    startedAt: "2026-08-23T16:34:00+08:00",
    finishedAt: "2026-08-23T16:35:00+08:00",
    errorClass: null,
  },
  items: [
    {
      id: "E-POST-001",
      itemType: "FACT",
      severity: "INFO",
      title: "决策事件 · 招商银行计划执行核对",
      body: "盘前计划：若银行板块放量并站稳观察位，保留仓位。用户 10:12 回复“继续持有”，当日无成交。",
      actionPlan: null,
      quality: "FRESH",
      evidence: [
        {
          id: "8af640d8-c0a3-4db6-b932-bf82f81f893a",
          claimType: "FACT",
          sourceSnapshotId: "37e6afe0-e420-4f85-8d06-9010bb488e4f",
          sourceName: "WealthSail-Fund-Estimator",
          originUri: "internal:worker:market",
          asOf: "2026-08-23T14:30:00+08:00",
          excerpt: "基金估值区间及披露覆盖率",
          quality: "ESTIMATED",
          contentHash: "sha256:8d6339f128bcf219c23eb7ad99fcebc3",
        },
      ],
    },
  ],
  versionHistory: [
    {
      id: "R-POST-20260823-v1",
      version: 1,
      status: "PUBLISHED",
      asOf: "2026-08-23T16:00:00+08:00",
      supersedesReportId: null,
    },
  ],
};

export const policiesFixture: PoliciesResponse = {
  current: {
    id: "pol-001",
    version: 3,
    status: "CONFIRMED",
    effectiveFrom: "2026-07-15",
    effectiveTo: null,
    confirmedAt: "2026-07-15T09:26:00+08:00",
    confirmedBy: "李明远",
    confirmationProvenance: "USER_CONFIRMED",
    policyHash: "sha256:4e78…01a9",
    accountId: null,
  },
  history: [
    {
      id: "pol-001",
      version: 3,
      status: "CONFIRMED",
      effectiveFrom: "2026-07-15",
      effectiveTo: null,
      confirmedAt: "2026-07-15T09:26:00+08:00",
      confirmedBy: "李明远",
      confirmationProvenance: "USER_CONFIRMED",
      policyHash: "sha256:4e78…01a9",
      accountId: null,
    },
    {
      id: "pol-000",
      version: 2,
      status: "RETIRED",
      effectiveFrom: "2026-01-01",
      effectiveTo: "2026-07-14",
      confirmedAt: null,
      confirmedBy: null,
      confirmationProvenance: "LEGACY_MIGRATED",
      policyHash: "sha256:0000…0000",
      accountId: null,
    },
  ],
  budget: {
    maxDrawdownLimit: "15.00",
    singlePositionLimit: "30.00",
    equityTargetPct: "60.00",
    bondTargetPct: "20.00",
    goldTargetPct: "10.00",
    cashTargetPct: "10.00",
    rebalanceTolerancePct: "2.00",
    minCashRatio: "10.00",
    investmentHorizonMonths: 24,
    cashNeedAmount: null,
    allowedAssetTypes: null,
    alertBudgetPerDay: 5,
  },
};

export const rulesFixture: RulesResponse = {
  rules: [
    {
      id: "rule-001",
      ruleType: "PRICE_TOUCH_STOP_LOSS",
      name: "价格止损触点 · 科大讯飞",
      params: { stop_loss_pct: 8.0 },
      silenceWindowMinutes: 60,
      status: "ACTIVE",
      instrumentName: "科大讯飞",
      lastTriggeredAt: "2026-08-23T10:47:00+08:00",
    },
  ],
  checks: [
    {
      check: "singlePositionLimit",
      name: "单一标的最大仓位",
      limit: "30.00",
      current: "17.17",
      status: "OK",
      message: "最大单一持仓占比 17.17%，政策上限 30.00%",
    },
    {
      check: "maxDrawdown",
      name: "组合最大回撤",
      limit: "15.00",
      current: "4.20",
      status: "OK",
      message: "近 60 交易日峰值回撤 4.20%",
    },
    {
      check: "minCashRatio",
      name: "最低现金比例",
      limit: "10.00",
      current: "10.48",
      status: "OK",
      message: "现金占比 10.48%，目标 10.00%",
    },
  ],
};

export const meFixture: MeResponse = {
  profile: {
    id: "usr-owner-001",
    email: "zhangming@example.com",
    nickname: "张明",
    avatarUrl: null,
    createdAt: "2026-01-01T00:00:00+08:00",
  },
  activeTenantId: "tenant-demo-001",
  activeRole: "OWNER",
  tenants: [
    {
      tenantId: "tenant-demo-001",
      name: "明远家庭账户",
      role: "OWNER",
      status: "ACTIVE",
    },
  ],
  bindings: [],
};

export const opsJobsFixture: OpsJobsResponse = {
  jobs: [
    {
      id: "job-001",
      jobName: "report.postmarket.generate",
      cronExpr: "0 30 15 * * 1-5",
      reportSession: "POST_MARKET",
      isEnabled: true,
    },
  ],
  runs: [
    {
      id: "run-001",
      jobId: "job-001",
      jobKey: "report.postmarket.generate:2026-08-23:162000",
      startedAt: "2026-08-23T16:20:03+08:00",
      finishedAt: "2026-08-23T16:20:45+08:00",
      status: "COMPLETED",
      errorClass: null,
      durationSeconds: 42.8,
    },
  ],
};

export const opsFreshnessFixture: FreshnessResponse = {
  nav: { latestAsOf: "2026-08-23T16:00:08+08:00", freshCount: 1, total: 1 },
  positions: { latestAsOf: "2026-08-23T16:00:08+08:00", freshCount: 6, total: 6 },
};

export const opsOutboxFixture: OutboxResponse = {
  counts: { pending: 1, sending: 0, sent: 10, dead: 0 },
  recent: [],
};

export const auditFixture: AuditRow[] = [
  {
    id: "aud-001",
    actorType: "USER",
    actorId: "usr-owner-001",
    action: "policy.confirm",
    resourceType: "risk_policy",
    resourceId: "pol-004",
    correlationId: "req_7e91a2",
    createdAt: "2026-08-23T23:07:42+08:00",
  },
];

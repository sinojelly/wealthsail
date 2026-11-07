import type { ActionableZoneResult } from "../../api/investment";
import type {
  HeldFundWatch,
  StudyDetail,
  StudyTrack,
} from "../../api/study-api";

export function frozenPlan(
  code = "024194",
  low = "1.0500",
): ActionableZoneResult {
  return {
    methodVersion: "frozen-nav-v1",
    subjectKind: "nav",
    subjectId: code,
    fundCode: code,
    market: "FUND_CN",
    label: `基金${code}`,
    priceBasis: "UNIT_NAV",
    unit: "CNY/份",
    asOf: "2026-10-20",
    sourceUri: "https://example.org/frozen-nav",
    currentPrice: "1.1200",
    selectedTrack: "LEFT_VALUE",
    indicators: {},
    tracks: [
      {
        track: "LEFT_VALUE",
        state: "CANDIDATE",
        priceState: "READY",
        entryZoneMin: low,
        entryZoneMax: "1.1500",
        stopLossPrice: "0.9500",
        takeProfitT1: "1.3500",
        takeProfitT2: "1.5000",
        reasons: [],
      },
    ],
  };
}

export function fundTrack(id = "study-1", code = "024194"): StudyTrack {
  return {
    id,
    title: `研究${id}`,
    enabled: true,
    generation: 3,
    status: "OBSERVING",
    createdAt: "2026-10-21T01:00:00Z",
    updatedAt: "2026-11-07T01:00:00Z",
    expiresOn: "2026-11-20",
    unreadCount: 0,
    scope: { fundCode: code, fundMarket: "FUND_CN" },
    priceWatch: {
      frozenPlan: frozenPlan(code),
      confirmedNav: {
        value: "1.1410",
        asOf: "2026-11-06",
        unit: "CNY/份",
        priceBasis: "UNIT_NAV",
        sourceUri: "https://example.org/nav",
        usable: true,
      },
      estimate1430: null,
      currentEstimate: {
        value: "1.1600",
        asOf: "2026-11-07T06:30:00Z",
        unit: "CNY/份",
        priceBasis: "UNIT_NAV",
        sourceUri: "https://example.org/estimate",
        usable: true,
      },
      holding: { state: "NONE" },
      state: "ENTRY_ZONE",
      basis: "CONFIRMED_NAV",
      asOf: "2026-11-06",
      reason: "已核对原冻结计划与确认净值",
    },
  };
}

export function heldFund(code = "024194"): HeldFundWatch {
  return {
    fundCode: code,
    fundMarket: "FUND_CN",
    instrumentId: `instrument-${code}`,
    label: `持仓基金${code}`,
    confirmedNav: fundTrack("held", code).priceWatch!.confirmedNav,
  };
}

export function studyDetail(
  track: StudyTrack,
  next = "等待后续披露",
): StudyDetail {
  return {
    track,
    version: {
      id: `version-${track.id}`,
      version: 2,
      generation: track.generation,
      createdAt: track.createdAt,
      snapshot: {
        title: track.title,
        thesis: "原始研究逻辑",
        transmission: [],
        supports: [],
        counterFactors: [],
        watchFor: [next],
        invalidationConditions: [],
        sources: [],
        origin: { originType: "SELECTION" },
        scope: track.scope,
        createdAt: track.createdAt,
        expiresOn: track.expiresOn!,
        conditions: [],
        observations: [],
      },
    },
    versions: [],
    checks: [],
    notes: [],
    tradeLinks: [],
    history: { limit: 50, offset: 0, hasMore: false },
    capabilities: { canEdit: false, canCheck: false, canLinkTrade: false },
  };
}

import {
  groupPersonalFunds,
  personalFundPriority,
  sortPersonalFunds,
} from "./personal-fund-model";
import type { PersonalFundReference } from "../../api/personal-funds";
import { frozenPlan, fundTrack, heldFund } from "./personal-funds.test-data";

const today = "2026-11-07";

function reference(code: string, relationship = "WATCHLIST", current = "1") {
  const plan = frozenPlan(code);
  plan.currentPrice = current;
  plan.tracks[0].entryZoneMin = "0.90";
  plan.tracks[0].entryZoneMax = "0.98";
  plan.tracks[0].takeProfitT1 = "1.02";
  plan.tracks[0].takeProfitT2 = "1.20";
  plan.tracks[0].stopLossPrice = "0.80";
  return {
    key: `FUND_CN:${code}`,
    fundCode: code,
    fundMarket: "FUND_CN",
    label: code,
    relationships: [relationship],
    plan,
    confirmedNav: null,
    currentEstimate: null,
    history: null,
    missingReasons: [],
    investmentEligible: false,
  } as PersonalFundReference;
}

it("does not create a personal fund from a research mention returned by an old server", () => {
  expect(
    groupPersonalFunds({ items: [], total: 0 }, today, [
      reference("000051", "RESEARCH_MENTIONED"),
    ]),
  ).toEqual([]);
});

it("prioritizes held stop/sell references before near buy opportunities and folds an exact 2 percent boundary", () => {
  const groups = groupPersonalFunds({ items: [], total: 0 }, today, [
    reference("000001", "HELD", "1"),
    reference("000002", "WATCHLIST", "0.999"),
    reference("000003", "HELD", "1.015"),
    reference("000004", "HELD", "0.79"),
    reference("000005", "WATCHLIST", "0.998"),
    reference("000006", "HELD", "0.95"),
  ]);
  expect(sortPersonalFunds(groups).map((row) => row.code)).toEqual([
    "000004",
    "000003",
    "000006",
    "000005",
    "000002",
    "000001",
  ]);
  expect(personalFundPriority(groups[0]).defaultExpanded).toBe(false);
  expect(personalFundPriority(groups[2]).defaultExpanded).toBe(true);
  expect(personalFundPriority(groups[3]).label).toContain("止损");
});

it("does not promote a stopped watchlist fund even when it is within 2 percent of entry", () => {
  const item = reference("000001", "WATCHLIST", "0.89");
  item.plan!.tracks[0].stopLossPrice = "0.895";
  const [group] = groupPersonalFunds({ items: [], total: 0 }, today, [item]);
  expect(personalFundPriority(group)).toMatchObject({
    stopTriggered: true,
    defaultExpanded: false,
    label: "跌破止损参考",
  });
});

it("aggregates a holding and two research plans once without merging frozen prices", () => {
  const first = fundTrack("first");
  const second = fundTrack("second");
  second.priceWatch!.frozenPlan = frozenPlan("024194", "1.0800");
  const groups = groupPersonalFunds(
    { items: [first, second], total: 2, heldFunds: [heldFund()] },
    today,
  );
  expect(groups).toHaveLength(1);
  expect(groups[0].holding?.instrumentId).toBe("instrument-024194");
  expect(
    groups[0].tracks.map(
      (row) => row.priceWatch?.frozenPlan?.tracks[0].entryZoneMin,
    ),
  ).toEqual(["1.0500", "1.0800"]);
});

it("keeps equal codes in different markets separate", () => {
  const etf = fundTrack("etf");
  etf.scope.fundMarket = "ETF_CN";
  const groups = groupPersonalFunds(
    { items: [fundTrack(), etf], total: 2 },
    today,
  );
  expect(groups.map((row) => row.key)).toEqual([
    "FUND_CN:024194",
    "ETF_CN:024194",
  ]);
});

it("keeps code-less holdings identified by instrument without inventing a code", () => {
  const holding = { ...heldFund(), fundCode: null };
  const groups = groupPersonalFunds(
    { items: [], total: 0, heldFunds: [holding] },
    today,
  );
  expect(groups[0]).toMatchObject({
    key: "FUND_CN:instrument:instrument-024194",
    market: "FUND_CN",
    holding,
    trackingSuppressed: false,
  });
  expect(groups[0].code).toBeUndefined();
});

it("retains real holdings when tracking is suppressed by code or instrument", () => {
  const groups = groupPersonalFunds(
    {
      items: [fundTrack()],
      total: 1,
      heldFunds: [heldFund()],
      suppressedFundKeys: ["FUND_CN:instrument:instrument-024194"],
    },
    today,
  );
  expect(groups).toHaveLength(1);
  expect(groups[0].holding).toBeDefined();
  expect(groups[0].trackingSuppressed).toBe(true);
  expect(groups[0].tracks).toEqual([]);
});

it("excludes disabled, ended, expired and suppressed tracking but includes the Shanghai expiry day", () => {
  const disabled = { ...fundTrack("disabled", "000001"), enabled: false };
  const ended = { ...fundTrack("ended", "000002"), status: "ENDED" };
  const expired = {
    ...fundTrack("expired", "000003"),
    expiresOn: "2026-11-06",
  };
  const expiresToday = { ...fundTrack("today", "000004"), expiresOn: today };
  const suppressed = fundTrack("suppressed", "000005");
  expect(
    groupPersonalFunds(
      {
        items: [disabled, ended, expired, expiresToday, suppressed],
        total: 5,
        suppressedFundKeys: ["FUND_CN:000005"],
      },
      today,
    ).map((row) => row.code),
  ).toEqual(["000004"]);
});

it("never treats a tracked fund as a real holding or defaults an unknown market to domestic NAV", () => {
  const track = fundTrack();
  track.scope.fundMarket = undefined;
  track.priceWatch!.frozenPlan!.market = undefined;
  const groups = groupPersonalFunds({ items: [track], total: 1 }, today);
  expect(groups).toHaveLength(1);
  expect(groups[0].holding).toBeUndefined();
  expect(groups[0].market).toBe("UNKNOWN");
});

it("does not turn an index research record into a fund", () => {
  const index = fundTrack();
  index.priceWatch!.frozenPlan!.subjectKind = "index";
  index.scope = { indexId: "hs300" };
  expect(
    groupPersonalFunds(
      { items: [index, fundTrack("fund")], total: 2 },
      today,
    ).map((row) => row.tracks[0].id),
  ).toEqual(["fund"]);
});

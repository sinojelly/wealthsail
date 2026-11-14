import { fundOperationDistance } from "./fund-operation-distance";
import { frozenPlan } from "./personal-funds.test-data";

it("measures actual interval distances and keeps the strict two percent boundary", () => {
  const plan = frozenPlan();
  plan.tracks[0].entryZoneMax = "98";
  plan.tracks[0].entryZoneMin = "90";
  plan.tracks[0].takeProfitT1 = "102";
  plan.tracks[0].takeProfitT2 = "110";
  plan.tracks[0].stopLossPrice = "80";
  const position = fundOperationDistance(plan, "100");
  expect(position.buyDistance).toBe(2);
  expect(position.buyDistance! < 2).toBe(false);
  expect(position.sellDistance).toBe(2);
  expect(position.inBuyZone).toBe(false);
  expect(fundOperationDistance(plan, "99.9").buyDistance).toBeLessThan(2);
});

it("does not round an exact decimal two percent boundary into an urgent fund", () => {
  const plan = frozenPlan();
  plan.tracks[0].entryZoneMin = "0.9";
  plan.tracks[0].entryZoneMax = "0.98";
  plan.tracks[0].takeProfitT1 = "1.02";
  plan.tracks[0].takeProfitT2 = "1.1";
  expect(fundOperationDistance(plan, "1")).toMatchObject({
    buyDistance: 2,
    sellDistance: 2,
  });
});

it("keeps stop loss and crossed sell references explicit, without using a foreign price", () => {
  const plan = frozenPlan();
  expect(fundOperationDistance(plan, "0.9").stopTriggered).toBe(true);
  expect(fundOperationDistance(plan, "1.6")).toMatchObject({
    inSellZone: true,
    sellDistance: 0,
  });
  expect(
    fundOperationDistance({ ...plan, unit: "USD" }).buyDistance,
  ).toBeNull();
  expect(
    fundOperationDistance({ ...plan, tracks: [] }).sellDistance,
  ).toBeNull();
});

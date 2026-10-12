import { chooseFund, chooseIndex, locateSearch } from "./selection";

it("keeps the parent index when choosing a fund", () => {
  const state = chooseFund(
    {
      directionId: "a-share:broad",
      indexId: "hs300",
      fundId: null,
      fundCode: null,
    },
    { instrumentId: "fund-id", fundCode: "510330" },
  );
  expect(state.indexId).toBe("hs300");
  expect(state.fundCode).toBe("510330");
  expect(chooseIndex(state, "a500").fundId).toBeNull();
});

it("globally locates a result and does not guess an unknown parent", () => {
  expect(
    locateSearch({
      kind: "fund",
      id: "id",
      code: "159920",
      indexId: "hsi",
      directionId: "hk:broad",
    }),
  ).toEqual({
    directionId: "hk:broad",
    indexId: "hsi",
    fundId: "id",
    fundCode: "159920",
  });
  expect(
    locateSearch({
      kind: "fund",
      id: "id",
      code: "110022",
      indexId: null,
      directionId: null,
    }).indexId,
  ).toBeNull();
});

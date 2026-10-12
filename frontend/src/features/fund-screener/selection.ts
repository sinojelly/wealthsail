export interface Selection {
  directionId: string | null;
  indexId: string | null;
  fundId: string | null;
  fundCode: string | null;
}
export function chooseFund(
  state: Selection,
  fund: { instrumentId: string | null; fundCode: string },
): Selection {
  return { ...state, fundId: fund.instrumentId, fundCode: fund.fundCode };
}
export function chooseIndex(state: Selection, indexId: string): Selection {
  return { ...state, indexId, fundId: null, fundCode: null };
}
export function locateSearch(result: {
  kind: "index" | "fund";
  id: string;
  code: string;
  indexId: string | null;
  directionId: string | null;
}): Selection {
  return {
    directionId: result.directionId,
    indexId: result.indexId,
    fundId: result.kind === "fund" ? result.id : null,
    fundCode: result.kind === "fund" ? result.code : null,
  };
}

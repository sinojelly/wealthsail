export interface Selection {
  directionId: string | null;
  indexId: string | null;
  fundId: string | null;
  fundCode: string | null;
  fundMarket?: string | null;
}
export function chooseFund(
  state: Selection,
  fund: { instrumentId: string | null; fundCode: string; market?: string },
): Selection {
  return {
    ...state,
    fundId: fund.instrumentId,
    fundCode: fund.fundCode,
    ...(fund.market ? { fundMarket: fund.market } : {}),
  };
}
export function chooseIndex(state: Selection, indexId: string): Selection {
  return { ...state, indexId, fundId: null, fundCode: null, fundMarket: null };
}
export function locateSearch(result: {
  kind: "index" | "fund";
  id: string;
  code: string;
  indexId: string | null;
  directionId: string | null;
  market?: string;
}): Selection {
  return {
    directionId: result.directionId,
    indexId: result.indexId,
    fundId: result.kind === "fund" ? result.id : null,
    fundCode: result.kind === "fund" ? result.code : null,
    ...(result.kind === "fund" && result.market
      ? { fundMarket: result.market }
      : {}),
  };
}

export function resolveFund<
  T extends {
    instrumentId?: string | null;
    fundCode?: string;
    code?: string;
    market?: string;
  },
>(state: Selection, candidates: T[]): T | undefined {
  const found = candidates.filter((f) =>
    state.fundId
      ? f.instrumentId === state.fundId
      : (f.fundCode ?? f.code) === state.fundCode &&
        (!state.fundMarket || f.market === state.fundMarket),
  );
  const unique = new Map(
    found.map((f) => [
      f.instrumentId ?? `${f.market}:${f.fundCode ?? f.code}`,
      f,
    ]),
  );
  return unique.size === 1 ? [...unique.values()][0] : undefined;
}

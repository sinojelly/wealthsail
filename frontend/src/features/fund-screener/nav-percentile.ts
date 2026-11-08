/** Same EMPIRICAL_MIDRANK_V1 definition as the persisted NAV read model. */
export function navPercentile(values: readonly number[]): number | null {
  if (
    !values.length ||
    values.some((value) => !Number.isFinite(value) || value <= 0)
  )
    return null;
  const current = values.at(-1)!;
  const rank = values.reduce(
    (total, value) =>
      total + (value < current ? 1 : value === current ? 0.5 : 0),
    0,
  );
  return Math.round((rank / values.length) * 1_000_000) / 10_000;
}

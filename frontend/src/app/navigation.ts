export function loginRedirectPath(
  pathname: string,
  search = "",
  hash = "",
): string {
  return `/login?returnTo=${encodeURIComponent(`${pathname}${search}${hash}`)}`;
}

/** Only allow a same-site path to survive the authentication boundary. */
export function safeReturnTo(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return "/dashboard";
  }
  try {
    const candidate = new URL(value, "https://wealthsail.invalid");
    if (candidate.origin !== "https://wealthsail.invalid") return "/dashboard";
    return `${candidate.pathname}${candidate.search}${candidate.hash}`;
  } catch {
    return "/dashboard";
  }
}

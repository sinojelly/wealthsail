/** Load the UI smoke target without embedding credentials in the repository. */
export function loadUiSmokeConfig(env = process.env) {
  const baseUrl = env.WS_SMOKE_BASE_URL?.trim().replace(/\/$/, "");
  const email = env.WS_SMOKE_EMAIL?.trim();
  const password = env.WS_SMOKE_PASSWORD;

  if (!baseUrl) {
    throw new Error("WS_SMOKE_BASE_URL is required");
  }
  if (!email || !password) {
    throw new Error("WS_SMOKE_EMAIL and WS_SMOKE_PASSWORD are required");
  }

  let target;
  try {
    target = new URL(baseUrl);
  } catch {
    throw new Error("WS_SMOKE_BASE_URL must be an absolute URL");
  }
  const loopback = new Set(["localhost", "127.0.0.1", "::1"]);
  if (target.protocol !== "https:" && !loopback.has(target.hostname)) {
    throw new Error("Remote UI smoke targets must use HTTPS");
  }

  return Object.freeze({ baseUrl, email, password, readOnly: true });
}

const ALLOWED_AUTH_WRITES = new Set([
  "/api/v1/auth/login",
  "/api/v1/auth/refresh",
  "/api/v1/auth/logout",
]);

/**
 * Enforce the smoke journey's read-only business boundary at the browser edge.
 * Login/refresh/logout write only authentication lifecycle records; every
 * portfolio, report, risk and settings command remains blocked.
 */
export function assertSafeSmokeRequest(requestUrl, method, baseUrl) {
  const target = new URL(requestUrl);
  const base = new URL(baseUrl);
  if (!target.pathname.startsWith("/api/")) return;
  if (target.origin !== base.origin) {
    throw new Error(`cross-origin API request blocked: ${target.origin}`);
  }
  const normalizedMethod = method.toUpperCase();
  if (["GET", "HEAD", "OPTIONS"].includes(normalizedMethod)) return;
  if (normalizedMethod === "POST" && ALLOWED_AUTH_WRITES.has(target.pathname)) return;
  throw new Error(`unsafe write blocked: ${normalizedMethod} ${target.pathname}`);
}

/** The SPA probes refresh once before it knows whether an anonymous cookie exists. */
export function isExpectedAnonymousRefreshFailure(pathname, status, hasLoggedIn) {
  return pathname === "/api/v1/auth/refresh" && status === 401 && !hasLoggedIn;
}

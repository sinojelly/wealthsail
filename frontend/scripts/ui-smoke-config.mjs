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

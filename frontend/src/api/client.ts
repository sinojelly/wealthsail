import type { ApiEnvelope, ApiMeta } from "./types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";
let accessToken: string | null = null;
let refreshAccessToken: (() => Promise<string | null>) | null = null;
let authFailureHandler: (() => Promise<void> | void) | null = null;
let refreshInFlight: Promise<string | null> | null = null;

export class HttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly requestId?: string,
  ) {
    super(message);
  }
}

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function configureAuthLifecycle(options: {
  refresh: () => Promise<string | null>;
  onAuthFailure: () => Promise<void> | void;
}) {
  refreshAccessToken = options.refresh;
  authFailureHandler = options.onAuthFailure;
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<ApiEnvelope<T>> {
  return requestOnce<T>(path, init, false);
}

async function requestOnce<T>(
  path: string,
  init: RequestInit,
  hasRetriedAuth: boolean,
): Promise<ApiEnvelope<T>> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  const responseText = await response.text();
  let body: {
    data?: T;
    meta?: ApiMeta;
    error?: { code?: string; message?: string; fieldErrors?: unknown };
    requestId?: string;
    message?: string;
  };
  try {
    body = responseText ? JSON.parse(responseText) : {};
  } catch {
    body = {};
  }
  if (
    response.status === 401 &&
    !hasRetriedAuth &&
    refreshAccessToken &&
    !path.startsWith("/v1/auth/")
  ) {
    refreshInFlight ??= refreshAccessToken().finally(() => {
      refreshInFlight = null;
    });
    const refreshed = await refreshInFlight;
    if (refreshed) return requestOnce<T>(path, init, true);
  }
  if (!response.ok) {
    if (response.status === 401 && authFailureHandler) {
      await authFailureHandler();
    }
    throw new HttpError(
      body.error?.message ?? body.message ?? "请求失败",
      response.status,
      body.error?.code,
      body.requestId,
    );
  }
  if (!("data" in body) || !body.meta) {
    throw new HttpError(
      "服务端响应格式无效",
      response.status,
      "INVALID_RESPONSE_ENVELOPE",
      response.headers.get("X-Request-Id") ?? undefined,
    );
  }
  return body as ApiEnvelope<T>;
}

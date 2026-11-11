import type { ApiEnvelope, ApiFieldError, ApiMeta } from "./types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";
let accessToken: string | null = null;
let refreshAccessToken: (() => Promise<string | null>) | null = null;
let authFailureHandler: (() => Promise<void> | void) | null = null;
let refreshInFlight: Promise<string | null> | null = null;

function logRequestTiming(
  path: string,
  init: RequestInit,
  started: number,
  headersAt: number,
  bodyAt: number,
  parsedAt: number,
  response?: Response,
  failurePhase?: "HEADERS" | "BODY",
) {
  if (new URLSearchParams(window.location.search).get("perf") !== "1") return;
  const server = response?.headers.get("X-Request-Time-Ms");
  const ms = (value: number) => Math.round(value * 10) / 10;
  console.info(
    "frontend_performance",
    JSON.stringify({
      phase: "api",
      path: path.split(/[?#]/, 1)[0],
      method: init.method ?? "GET",
      status: response?.status ?? 0,
      requestId: response?.headers.get("X-Request-Id") ?? null,
      headersMs: ms(headersAt - started),
      bodyMs: ms(bodyAt - headersAt),
      parseMs: ms(parsedAt - bodyAt),
      durationMs: ms(parsedAt - started),
      serverMs:
        server && Number.isFinite(Number(server)) ? Number(server) : null,
      serverTiming: response?.headers.get("Server-Timing") ?? null,
      failurePhase: failurePhase ?? null,
    }),
  );
}

export class HttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly requestId?: string,
    readonly fieldErrors: ApiFieldError[] = [],
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

// Opt in for saved-data reads; research submissions keep their own lifecycle.
// The deadline covers both HTTP headers and body, including authentication retry.
export async function requestWithTimeout<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 30_000,
): Promise<ApiEnvelope<T>> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel: (() => void) | undefined;
  try {
    return await Promise.race([
      new Promise<never>((_resolve, reject) => {
        cancel = () => {
          const reason =
            init.signal?.reason ??
            new DOMException("Request cancelled", "AbortError");
          reject(reason);
          controller.abort(reason);
        };
        if (init.signal?.aborted) cancel();
        else init.signal?.addEventListener("abort", cancel, { once: true });
        timer = setTimeout(() => {
          const failure = new HttpError(
            "读取服务端数据超时",
            408,
            "REQUEST_TIMEOUT",
          );
          reject(failure);
          controller.abort(failure);
        }, timeoutMs);
      }),
      request<T>(path, { ...init, signal: controller.signal }),
    ]);
  } finally {
    clearTimeout(timer);
    if (cancel) init.signal?.removeEventListener("abort", cancel);
  }
}

async function requestOnce<T>(
  path: string,
  init: RequestInit,
  hasRetriedAuth: boolean,
): Promise<ApiEnvelope<T>> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (
    init.body &&
    !(init.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  const started = performance.now();
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers,
      credentials: "include",
    });
  } catch (failure) {
    const failedAt = performance.now();
    logRequestTiming(
      path,
      init,
      started,
      failedAt,
      failedAt,
      failedAt,
      undefined,
      "HEADERS",
    );
    throw failure;
  }
  const headersAt = performance.now();
  let responseText: string;
  try {
    responseText = await response.text();
  } catch (failure) {
    const failedAt = performance.now();
    logRequestTiming(
      path,
      init,
      started,
      headersAt,
      failedAt,
      failedAt,
      response,
      "BODY",
    );
    throw failure;
  }
  const bodyAt = performance.now();
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
  logRequestTiming(
    path,
    init,
    started,
    headersAt,
    bodyAt,
    performance.now(),
    response,
  );
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
      Array.isArray(body.error?.fieldErrors)
        ? (body.error.fieldErrors as ApiFieldError[])
        : [],
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

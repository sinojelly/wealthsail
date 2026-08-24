import type { ApiEnvelope, ApiMeta } from "./types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";
let accessToken: string | null = null;

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

export async function request<T>(
  path: string,
  init: RequestInit = {},
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
  const body = (await response.json()) as {
    data?: T;
    meta?: ApiMeta;
    error?: { code?: string; message?: string; fieldErrors?: unknown };
    requestId?: string;
    message?: string;
  };
  if (!response.ok) {
    throw new HttpError(
      body.error?.message ?? body.message ?? "请求失败",
      response.status,
      body.error?.code,
      body.requestId,
    );
  }
  return body as ApiEnvelope<T>;
}

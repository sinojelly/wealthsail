import { API_BASE_URL, getAccessToken, request } from "./client";
import type {
  BindingCredential,
  LoginResponse,
  MeProfile,
  MeResponse,
} from "./types";

export async function login(
  email: string,
  password: string,
): Promise<LoginResponse> {
  const envelope = await request<LoginResponse>("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  return envelope.data;
}

export async function register(input: {
  email: string;
  password: string;
  nickname?: string;
}): Promise<{ userId: string; email: string; nickname: string; tenantId: string; tenantName: string }> {
  const envelope = await request<{
    userId: string;
    email: string;
    nickname: string;
    tenantId: string;
    tenantName: string;
  }>("/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return envelope.data;
}

export async function getMe(): Promise<MeResponse> {
  const envelope = await request<MeResponse>("/v1/auth/me");
  return envelope.data;
}

/** 更新当前用户资料（昵称 / 头像 URL）。 */
export async function updateProfile(input: {
  nickname?: string;
  avatarUrl?: string;
}): Promise<MeProfile> {
  const envelope = await request<MeProfile>("/v1/auth/profile", {
    method: "PATCH",
    body: JSON.stringify(input),
  });
  return envelope.data;
}

/** 上传头像（multipart）；成功后后端已同步 avatar_url。 */
export async function uploadAvatar(file: File): Promise<{ avatarUrl: string }> {
  const form = new FormData();
  form.append("file", file);
  const token = getAccessToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  const resp = await fetch(`${API_BASE_URL}/v1/auth/avatar`, {
    method: "POST",
    headers,
    body: form,
    credentials: "include",
  });
  const body = (await resp.json()) as {
    data?: { avatarUrl: string };
    error?: { message?: string };
  };
  if (!resp.ok || !body.data) {
    throw new Error(body.error?.message ?? `头像上传失败 (${resp.status})`);
  }
  return body.data;
}

/** 创建企业微信一次性绑定口令（明文仅返回一次，库中只存 HMAC 摘要）。 */
export async function createBindingCredential(): Promise<BindingCredential> {
  const envelope = await request<BindingCredential>("/v1/auth/wecom/binding", {
    method: "POST",
    body: JSON.stringify({}),
  });
  return envelope.data;
}

/** 消费一次性绑定口令（企微回调/模拟确认路径）。 */
export async function consumeBindingCredential(input: {
  token: string;
  externalUserId: string;
  externalUsername?: string;
}): Promise<{ bindingId: string; status: string; channelType: string }> {
  const envelope = await request<{
    bindingId: string;
    status: string;
    channelType: string;
  }>("/v1/auth/wecom/binding", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return envelope.data;
}

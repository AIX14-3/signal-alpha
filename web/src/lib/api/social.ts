// 소셜 연동.

import { apiFetch } from "./core";
import type { AuthResult } from "./auth";

export type Provider = "naver" | "google" | "kakao";
export type SocialLink = { provider: Provider; linked: boolean; linked_at?: string | null };

export async function listSocial(): Promise<{ items: SocialLink[] }> {
  return apiFetch("/api/auth/social");
}

export async function linkSocial(
  provider: Provider,
  body: { code: string; redirect_uri?: string; state?: string },
): Promise<SocialLink> {
  return apiFetch(`/api/auth/social/link/${provider}`, { method: "POST", body: JSON.stringify(body) });
}

export async function socialLogin(
  provider: Provider,
  body: { code: string; redirect_uri?: string; state?: string },
): Promise<AuthResult> {
  return apiFetch(`/api/auth/social/login/${provider}`, {
    method: "POST",
    auth: "none",
    body: JSON.stringify(body),
  });
}

export async function unlinkSocial(provider: Provider): Promise<SocialLink> {
  return apiFetch(`/api/auth/social/${provider}`, { method: "DELETE" });
}

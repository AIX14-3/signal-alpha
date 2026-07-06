// 인증(포트원 V2 본인인증) + 내 계정.

import { apiFetch } from "./core";

export type User = {
  id: number;
  member_code: string | null;
  nickname: string | null;
  email: string | null;
  phone_masked: string | null;
  agreed_risk: boolean;
  subscription_active: boolean;
};

export type AuthResult = {
  user: User;
  access_token: string;
  // refresh 토큰은 HttpOnly 쿠키로만 전달된다(응답 body 에는 없음).
  refresh_token?: string;
  token_type?: string;
  notice?: string;
};

export async function signup(body: {
  identity_verification_id: string;
  email: string;
  nickname: string;
  agreed_risk: boolean;
  agreed_terms?: string[];
}): Promise<AuthResult> {
  return apiFetch("/api/auth/signup", { method: "POST", auth: "none", body: JSON.stringify(body) });
}

export async function login(body: { identity_verification_id: string }): Promise<AuthResult> {
  return apiFetch("/api/auth/login", { method: "POST", auth: "none", body: JSON.stringify(body) });
}

export async function logout(): Promise<void> {
  // refresh 토큰은 HttpOnly 쿠키로 자동 송신 → 서버가 쿠키를 읽어 세션 폐기·삭제한다.
  await apiFetch("/api/auth/logout", { method: "POST", auth: "none" });
}

export async function getMe(): Promise<User> {
  return apiFetch("/api/users/me");
}

export async function updateMe(body: { nickname?: string | null; email?: string | null }): Promise<User> {
  return apiFetch("/api/users/me", { method: "PATCH", body: JSON.stringify(body) });
}

export async function deleteMe(): Promise<{ status: string }> {
  return apiFetch("/api/users/me", { method: "DELETE" });
}

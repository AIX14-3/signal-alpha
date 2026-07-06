// main-server(:8000) API 클라이언트 코어 — 인증 헤더·fetch 래퍼·401 자동복구.
// 베이스 URL 은 NEXT_PUBLIC_MAIN_API_BASE_URL 로 주입.

import { clearUserTokens, getAccessToken, setUserTokens } from "@/lib/session";

export const MAIN_API_BASE_URL =
  process.env.NEXT_PUBLIC_MAIN_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  code?: string;
  status: number;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

type AuthMode = "user" | "admin" | "none";
type FetchInit = RequestInit & { auth?: AuthMode };

function authHeader(mode: AuthMode): Record<string, string> {
  // admin 은 sa_admin HttpOnly 쿠키로 인증되므로 헤더를 붙이지 않는다(credentials:include 가 송신).
  if (mode === "user") {
    const token = getAccessToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  }
  return {};
}

async function rawFetch(path: string, init: FetchInit): Promise<Response> {
  const { auth = "user", headers, ...rest } = init;
  return fetch(`${MAIN_API_BASE_URL}${path}`, {
    ...rest,
    credentials: "include", // refresh 토큰 HttpOnly 쿠키(sa_refresh) 송수신
    headers: {
      "Content-Type": "application/json",
      ...authHeader(auth),
      ...(headers as Record<string, string> | undefined),
    },
  });
}

async function parse<T>(res: Response): Promise<T> {
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail;
    const code = detail && typeof detail === "object" ? detail.code : undefined;
    const message =
      detail && typeof detail === "object"
        ? detail.message
        : typeof detail === "string"
          ? detail
          : res.statusText;
    throw new ApiError(message ?? "요청에 실패했습니다.", res.status, code);
  }
  return data as T;
}

// refresh 토큰은 HttpOnly 쿠키로 자동 송신되므로 body 가 필요 없다.
// 성공 시 새 access 토큰만 인메모리에 저장한다. 부팅 hydrate 와 401 자동복구가 함께 사용한다.
export async function refreshSession(): Promise<boolean> {
  const res = await rawFetch("/api/auth/refresh", { method: "POST", auth: "none" });
  if (!res.ok) {
    clearUserTokens();
    return false;
  }
  const data = (await res.json()) as { access_token: string };
  setUserTokens(data.access_token);
  return true;
}

export async function apiFetch<T>(path: string, init: FetchInit = {}): Promise<T> {
  let res = await rawFetch(path, init);
  if (res.status === 401 && (init.auth ?? "user") === "user") {
    const refreshed = await refreshSession();
    if (refreshed) res = await rawFetch(path, init);
  }
  return parse<T>(res);
}

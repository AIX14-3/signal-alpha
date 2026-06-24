// 소셜 OAuth 헬퍼.
// - real 모드(NEXT_PUBLIC_{PROVIDER}_CLIENT_ID 설정): provider 인증요청으로 리다이렉트하고
//   /auth/callback/{provider} 에서 code 를 받아 백엔드로 전달한다(토큰+고유 id 만 사용).
// - dev 모드(client_id 미설정): 기기 고정 가상 code 로 백엔드 연동/로그인(리다이렉트 없음).

import type { Provider } from "@/lib/apiClient";

export const SOCIAL_PROVIDERS: { key: Provider; label: string }[] = [
  { key: "naver", label: "네이버" },
  { key: "google", label: "구글" },
  { key: "kakao", label: "카카오" },
];

export type SocialIntent = "link" | "login";
const OAUTH_STATE_KEY = "sa_social_oauth";

const CLIENT_IDS: Record<Provider, string | undefined> = {
  naver: process.env.NEXT_PUBLIC_NAVER_CLIENT_ID,
  google: process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID,
  kakao: process.env.NEXT_PUBLIC_KAKAO_CLIENT_ID,
};

export function isSocialDevMode(provider: Provider): boolean {
  return !CLIENT_IDS[provider];
}

export function callbackUri(provider: Provider): string {
  return `${window.location.origin}/auth/callback/${provider}`;
}

function randomState(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
}

function authorizeUrl(provider: Provider, redirectUri: string, state: string): string {
  const clientId = CLIENT_IDS[provider] as string;
  const cb = encodeURIComponent(redirectUri);
  if (provider === "naver") {
    return `https://nid.naver.com/oauth2.0/authorize?response_type=code&client_id=${clientId}&redirect_uri=${cb}&state=${state}`;
  }
  if (provider === "kakao") {
    return `https://kauth.kakao.com/oauth/authorize?response_type=code&client_id=${clientId}&redirect_uri=${cb}&state=${state}`;
  }
  // google — 고유 sub만(openid)
  return `https://accounts.google.com/o/oauth2/v2/auth?response_type=code&client_id=${clientId}&redirect_uri=${cb}&scope=openid&state=${state}`;
}

/** real 모드: provider 인증요청으로 리다이렉트. intent/state/returnTo 를 sessionStorage 에 저장. */
export function startSocialOAuth(provider: Provider, intent: SocialIntent, returnTo?: string): void {
  const state = randomState();
  const redirectUri = callbackUri(provider);
  window.sessionStorage.setItem(
    OAUTH_STATE_KEY,
    JSON.stringify({ provider, intent, state, returnTo }),
  );
  window.location.href = authorizeUrl(provider, redirectUri, state);
}

export function readOAuthState(): {
  provider: Provider;
  intent: SocialIntent;
  state: string;
  returnTo?: string;
} | null {
  const raw = window.sessionStorage.getItem(OAUTH_STATE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function clearOAuthState(): void {
  window.sessionStorage.removeItem(OAUTH_STATE_KEY);
}

/** dev 모드 전용: 기기·provider 고정 가상 code(연동↔로그인 매칭). */
export function socialAuthCode(provider: Provider): string {
  if (typeof window === "undefined") return `dev_${provider}`;
  const key = `sa_social_${provider}`;
  let code = window.localStorage.getItem(key);
  if (!code) {
    code = `devsocial_${provider}_${Math.random().toString(36).slice(2)}`;
    window.localStorage.setItem(key, code);
  }
  return code;
}

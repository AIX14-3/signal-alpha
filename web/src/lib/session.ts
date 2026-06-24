// 토큰 보관 단일 출처. apiClient(요청)와 authStore(React 상태)가 공유한다.
// 순환 의존을 피하기 위해 토큰 자체는 이 모듈이 소유(localStorage 영속).

const ACCESS_KEY = "sa_access";
const REFRESH_KEY = "sa_refresh";
const ADMIN_KEY = "sa_admin_session";

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export function getAccessToken(): string | null {
  return isBrowser() ? window.localStorage.getItem(ACCESS_KEY) : null;
}

export function getRefreshToken(): string | null {
  return isBrowser() ? window.localStorage.getItem(REFRESH_KEY) : null;
}

export function setUserTokens(accessToken: string, refreshToken: string): void {
  if (!isBrowser()) return;
  window.localStorage.setItem(ACCESS_KEY, accessToken);
  window.localStorage.setItem(REFRESH_KEY, refreshToken);
}

export function clearUserTokens(): void {
  if (!isBrowser()) return;
  window.localStorage.removeItem(ACCESS_KEY);
  window.localStorage.removeItem(REFRESH_KEY);
}

export function getAdminToken(): string | null {
  return isBrowser() ? window.localStorage.getItem(ADMIN_KEY) : null;
}

export function setAdminToken(token: string): void {
  if (!isBrowser()) return;
  window.localStorage.setItem(ADMIN_KEY, token);
}

export function clearAdminToken(): void {
  if (!isBrowser()) return;
  window.localStorage.removeItem(ADMIN_KEY);
}

// 토큰 보관 단일 출처. apiClient(요청)와 authStore(React 상태)가 공유한다.
// - access 토큰: 인메모리(모듈 변수). XSS 영속 탈취를 막기 위해 localStorage 에 두지 않는다.
//   새로고침 시 사라지므로 부팅 hydrate 가 refresh 쿠키로 재발급한다.
// - refresh 토큰: 백엔드가 HttpOnly 쿠키(sa_refresh)로만 관리 → JS 접근 불가, 여기서 다루지 않는다.
// - admin 세션 토큰: 백엔드가 HttpOnly 쿠키(sa_admin)로 관리 → JS 접근 불가, 여기서 다루지 않는다.

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setUserTokens(token: string): void {
  accessToken = token;
}

export function clearUserTokens(): void {
  accessToken = null;
}

// 소셜 OAuth code 헬퍼.
// - dev 모드: 기기·provider 별 고정 code 를 만들어 mypage 연동과 login 간편로그인이 같은
//   provider_user_id 로 매칭되게 한다(백엔드 dev 가 code→provider_user_id 를 결정적으로 도출).
// - real 모드: 각 provider OAuth 리다이렉트로 받은 code 를 사용(후속 통합).

import type { Provider } from "@/lib/apiClient";

export const SOCIAL_PROVIDERS: { key: Provider; label: string }[] = [
  { key: "naver", label: "네이버" },
  { key: "google", label: "구글" },
  { key: "kakao", label: "카카오" },
];

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

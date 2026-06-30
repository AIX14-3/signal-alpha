import path from "node:path";
import type { NextConfig } from "next";

// 응답 보안 헤더(방어선). CSP 는 의도적으로 frame-ancestors/object-src/base-uri 만 제한한다 —
// default/script/connect 를 묶으면 OAuth 리다이렉트·PortOne 결제 SDK 가 깨질 수 있어 제외했다.
// XSS 1차 방어는 렌더 직전 http(s) 스킴 allowlist(lib/format.safeHttpUrl)가 담당한다.
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'" },
];

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  // 프로덕션 배포(Cloud Run/GCE)용 최소 산출물. `.next/standalone` 에 서버 + 추려진
  // node_modules 만 담겨 `node server.js` 로 구동된다(이미지 경량화). dev 에는 영향 없음.
  output: "standalone",
  // 모노레포 루트에도 lockfile이 있어 워크스페이스 루트 추론 경고가 뜬다.
  // 트레이싱 루트를 web/ 로 고정해 경고를 제거한다(standalone 산출물 경로도 web/ 기준).
  outputFileTracingRoot: path.join(__dirname),
  // 개발 모드 인디케이터 배지(data-next-badge-root) 숨김. dev 전용 UI.
  devIndicators: false,
};

export default nextConfig;

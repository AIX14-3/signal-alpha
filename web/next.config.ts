import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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

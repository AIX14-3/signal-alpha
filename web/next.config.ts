import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 모노레포 루트에도 lockfile이 있어 워크스페이스 루트 추론 경고가 뜬다.
  // 트레이싱 루트를 web/ 로 고정해 경고를 제거한다.
  outputFileTracingRoot: path.join(__dirname),
};

export default nextConfig;

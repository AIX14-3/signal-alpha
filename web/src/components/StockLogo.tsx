"use client";

import { useEffect, useState } from "react";
import { stockLogoUrl } from "@/lib/api/stocks";

// 종목 회사 로고 — 원형. 백엔드 stock_logo_published 발행 사본을 <img> 로 직접 부른다.
// 로고 미발행(404)·로드 실패·코드 부재 시 종목명/코드 이니셜 폴백을 그린다(레이아웃 불변).
// 발행 전이면 전 종목이 폴백 → DB 에 로고가 발행되면 자동으로 실제 로고로 바뀐다.
export function StockLogo({
  code,
  name,
  size = 24,
  className = "",
}: {
  code: string;
  name?: string | null;
  size?: number;
  className?: string;
}) {
  const [broken, setBroken] = useState(false);

  // 종목이 바뀌면 폴백 상태를 리셋(같은 컴포넌트 재사용 시 이전 실패가 남지 않게).
  useEffect(() => {
    setBroken(false);
  }, [code]);

  const dim = { width: size, height: size };

  if (!code || broken) {
    const initials = (name ?? code ?? "").trim().slice(0, 2) || "·";
    return (
      <span
        aria-hidden="true"
        style={{ ...dim, fontSize: Math.round(size * 0.42) }}
        className={`inline-grid shrink-0 place-items-center rounded-full border border-line bg-surface-2 font-bold leading-none text-muted ${className}`}
      >
        {initials}
      </span>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- 원격 바이트 서빙(next/image 최적화 불필요·CSP 단순화)
    <img
      src={stockLogoUrl(code)}
      alt={name ? `${name} 로고` : `${code} 로고`}
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      onError={() => setBroken(true)}
      style={dim}
      className={`shrink-0 rounded-full border border-line bg-white object-contain ${className}`}
    />
  );
}

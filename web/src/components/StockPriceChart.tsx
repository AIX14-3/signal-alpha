"use client";

import type { StockPricePoint } from "@/lib/apiClient";

// 일봉 종가 라인 차트(공개 가격 API 시리즈). 발행 러너가 동기화한 close 만 그린다.
// 유효 점 2개 미만이면 "차트 준비 중"(미동기화 종목 안전 처리 — NFR-5).
export function StockPriceChart({
  series,
  loading,
}: {
  series: StockPricePoint[];
  loading: boolean;
}) {
  const points = series
    .map((p) => ({ date: p.trade_date, close: p.close }))
    .filter((p): p is { date: string; close: number } => p.close != null);

  if (loading) {
    return <div className="h-[120px] animate-pulse rounded-[10px] bg-surface-2" />;
  }
  if (points.length < 2) {
    return (
      <div className="grid h-[120px] place-items-center rounded-[10px] bg-surface-2/60 text-[12px] text-muted">
        차트 준비 중
      </div>
    );
  }

  const W = 320;
  const H = 120;
  const PAD = 6;
  const closes = points.map((p) => p.close);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1;
  const coords = points.map((p, i) => {
    const x = points.length === 1 ? 0 : (i / (points.length - 1)) * W;
    const y = H - PAD - ((p.close - min) / span) * (H - PAD * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  const first = points[0].close;
  const last = points[points.length - 1].close;
  const up = last >= first;
  const changePct = first ? ((last - first) / first) * 100 : 0;

  return (
    <div data-flow="stock-price-chart">
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-[16px] font-extrabold text-navy">
          {last.toLocaleString()}
          <span className="ml-1 text-[11px] font-semibold text-muted">원</span>
        </span>
        <span className={`text-[12px] font-semibold ${up ? "text-red" : "text-sky-deep"}`}>
          {up ? "▲" : "▼"} {Math.abs(changePct).toFixed(2)}%
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="h-[120px] w-full"
        aria-hidden="true"
      >
        <polyline
          fill="none"
          stroke={up ? "#e11d48" : "#0284c7"}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          points={coords.join(" ")}
        />
      </svg>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { getReportPrices, type PriceSeries } from "@/lib/apiClient";

// 일봉 캔들스틱 차트(홈 실시간 분석 아코디언). 리포트 OHLC 시세(getReportPrices)를
// 컴팩트 SVG 로 그린다 — 몸통 rect + 꼬리 line. 홈 1a "오로라 글래스" 시안 §6.
// 한국 시장 관례색: 상승(종가≥시가)=빨강 / 하락=파랑. '시세' 문맥 전용으로,
// 방향성 배지(up=초록/down=빨강)와는 별개의 색 체계다(디자인 결정: 캔들만 빨강/파랑).
const KR_UP = "#dc2626";
const KR_DOWN = "#2563eb";
const COUNT = 44; // 표시할 최근 일봉 수

export function StockCandleChart({ stockCode }: { stockCode: string }) {
  const [data, setData] = useState<PriceSeries | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = { aborted: false };
    setLoading(true);
    getReportPrices(stockCode, "day")
      .then((d) => {
        if (token.aborted) return;
        setData(d);
        setLoading(false);
      })
      .catch(() => {
        if (token.aborted) return;
        setData(null);
        setLoading(false);
      });
    return () => {
      token.aborted = true;
    };
  }, [stockCode]);

  if (loading) {
    return <div className="h-[132px] animate-pulse rounded-[10px] bg-surface-2" />;
  }

  const bars = (data?.bars ?? []).slice(-COUNT);
  if (bars.length < 2) {
    return (
      <div className="grid h-[132px] place-items-center rounded-[10px] bg-surface-2/60 text-[12px] text-muted">
        차트 준비 중
      </div>
    );
  }

  // OHLC → SVG 좌표. preserveAspectRatio=none 로 가로 폭을 컨테이너에 맞춰 늘린다
  // (몸통 rect 폭도 함께 늘어남). 꼬리 line 은 non-scaling-stroke 로 1px 유지.
  const W = 400;
  const H = 96;
  const PAD = 6;
  const hi = Math.max(...bars.map((b) => b.high));
  const lo = Math.min(...bars.map((b) => b.low));
  const range = hi - lo || 1;
  const step = W / bars.length;
  const bodyW = Math.max(step * 0.6, 1);
  const y = (price: number) => PAD + ((hi - price) / range) * (H - PAD * 2);

  const up = (data?.change ?? 0) >= 0;

  return (
    <div data-flow="stock-candle-chart">
      <div className="mb-1 flex items-baseline justify-between">
        <span className="text-[16px] font-extrabold text-navy">
          {data?.last_price.toLocaleString("ko-KR")}
          <span className="ml-1 text-[11px] font-semibold text-muted">원</span>
        </span>
        <span className="text-[12px] font-semibold" style={{ color: up ? KR_UP : KR_DOWN }}>
          {up ? "▲" : "▼"} {Math.abs(data?.change_pct ?? 0).toFixed(2)}%
        </span>
      </div>
      <div className="rounded-[8px] border border-line bg-surface p-1.5">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          className="h-[96px] w-full"
          aria-hidden="true"
        >
          {bars.map((b, i) => {
            const cx = i * step + step / 2;
            const isUp = b.close >= b.open;
            const color = isUp ? KR_UP : KR_DOWN;
            const bodyTop = y(Math.max(b.open, b.close));
            const bodyH = Math.max(y(Math.min(b.open, b.close)) - bodyTop, 1);
            return (
              <g key={i}>
                <line
                  x1={cx}
                  x2={cx}
                  y1={y(b.high)}
                  y2={y(b.low)}
                  stroke={color}
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
                <rect x={cx - bodyW / 2} y={bodyTop} width={bodyW} height={bodyH} fill={color} />
              </g>
            );
          })}
        </svg>
      </div>
      {data?.is_demo && (
        <p className="mt-1 text-[10.5px] text-muted">* 데모용 예시 시세(실데이터 아님).</p>
      )}
    </div>
  );
}

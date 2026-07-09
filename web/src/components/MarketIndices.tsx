"use client";

import { useEffect, useState } from "react";
import { getMarketIndices, type IndexBar, type MarketIndex } from "@/lib/apiClient";

// item 7 — 리포트 상단 시장 지수 미니차트(코스피·코스닥·원/달러·VIX). 인라인 SVG 스파크라인.
function Sparkline({ bars, up }: { bars: IndexBar[]; up: boolean }) {
  if (bars.length < 2) return null;
  const vals = bars.map((b) => b.close);
  const min = Math.min(...vals);
  const range = Math.max(...vals) - min || 1;
  const w = 76;
  const h = 26;
  const pts = vals
    .map((v, i) => `${(i / (vals.length - 1)) * w},${h - ((v - min) / range) * h}`)
    .join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <polyline points={pts} fill="none" stroke={up ? "#16a34a" : "#dc2626"} strokeWidth={1.5} />
    </svg>
  );
}

function IndexCard({ idx, tone = "glass" }: { idx: MarketIndex; tone?: "glass" | "flat" }) {
  const up = idx.change >= 0;
  return (
    <div
      className={`flex items-center justify-between gap-2 rounded-[12px] p-3 ${
        tone === "glass" ? "glass hover-lift" : "border border-line bg-surface-2"
      }`}
    >
      <div>
        <div className="text-[12px] font-semibold text-muted">{idx.name}</div>
        <div className="text-[15px] font-extrabold leading-tight text-navy">
          {idx.last.toLocaleString("ko-KR")}
        </div>
        <div className={`text-[11.5px] font-bold ${up ? "text-[#16a34a]" : "text-[#dc2626]"}`}>
          {up ? "▲" : "▼"} {Math.abs(idx.change_pct)}%
        </div>
      </div>
      <Sparkline bars={idx.bars} up={up} />
    </div>
  );
}

// variant "grid" = 리포트 상단(기존, 유리 카드 4열). "band" = 홈 대시보드 상단 가로 밴드(제목+실시간 배지 패널).
export function MarketIndices({ variant = "grid" }: { variant?: "grid" | "band" }) {
  const [items, setItems] = useState<MarketIndex[] | null>(null);

  useEffect(() => {
    let alive = true;
    getMarketIndices()
      .then((d) => alive && setItems(d.indices))
      .catch(() => alive && setItems([]));
    return () => {
      alive = false;
    };
  }, []);

  if (variant === "band") {
    return (
      <section data-section="market-indices" className="glass-card p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-[14px] font-bold text-navy">시장 지수</h2>
          <span className="flex items-center gap-1.5 text-[11.5px] text-muted">
            <span className="live-dot" /> 실시간
          </span>
        </div>
        {!items ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="h-[74px] animate-pulse rounded-[12px] bg-surface-2" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <p className="px-1 py-6 text-center text-[12.5px] text-muted">시장 지수를 불러오지 못했습니다.</p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {items.map((idx) => (
              <IndexCard key={idx.key} idx={idx} tone="flat" />
            ))}
          </div>
        )}
      </section>
    );
  }

  if (!items || items.length === 0) return null;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-section="market-indices">
      {items.map((idx) => (
        <IndexCard key={idx.key} idx={idx} tone="glass" />
      ))}
    </div>
  );
}

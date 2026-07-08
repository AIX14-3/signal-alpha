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

export function MarketIndices() {
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

  if (!items || items.length === 0) return null;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-section="market-indices">
      {items.map((idx) => {
        const up = idx.change >= 0;
        return (
          <div key={idx.key} className="glass hover-lift flex items-center justify-between gap-2 p-3">
            <div>
              <div className="text-[12px] font-semibold text-muted">{idx.name}</div>
              <div className="text-[15px] font-extrabold leading-tight">
                {idx.last.toLocaleString("ko-KR")}
              </div>
              <div className={`text-[11.5px] font-bold ${up ? "text-[#16a34a]" : "text-[#dc2626]"}`}>
                {up ? "▲" : "▼"} {Math.abs(idx.change_pct)}%
              </div>
            </div>
            <Sparkline bars={idx.bars} up={up} />
          </div>
        );
      })}
    </div>
  );
}

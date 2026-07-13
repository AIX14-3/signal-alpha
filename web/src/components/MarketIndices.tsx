"use client";

import { useEffect, useState } from "react";
import { getMarketIndices, type IndexBar, type MarketIndex } from "@/lib/apiClient";

// 지수도 '시세'라 한국 시장 관례를 따른다(상승=빨강, 하락=파랑). StockChart 의 KR_UP/KR_DOWN 과
// 같은 색 — 제품 전반의 방향 의미색(up=초록/down=빨강)과는 문맥이 다르다.
const KR_UP = "#ef4444";
const KR_DOWN = "#3b82f6";

// item 7 — 시장 지수 미니차트. 종가 선 + 하단 면적 그라데이션(프리미엄 룩). idKey 로 그라데이션 id 충돌 방지.
function Sparkline({ bars, up, idKey }: { bars: IndexBar[]; up: boolean; idKey: string }) {
  if (bars.length < 2) return null;
  const vals = bars.map((b) => b.close);
  const min = Math.min(...vals);
  const range = Math.max(...vals) - min || 1;
  const w = 88;
  const h = 36;
  const pad = 3;
  const coords = vals.map(
    (v, i) => [(i / (vals.length - 1)) * w, h - pad - ((v - min) / range) * (h - pad * 2)] as const,
  );
  const line = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `0,${h} ${line} ${w},${h}`;
  const color = up ? KR_UP : KR_DOWN;
  const gid = `spark-${idKey}`;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" className="shrink-0">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.22" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={area} fill={`url(#${gid})`} stroke="none" />
      <polyline
        points={line}
        fill="none"
        stroke={color}
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IndexCard({ idx, tone = "glass" }: { idx: MarketIndex; tone?: "glass" | "flat" }) {
  const up = idx.change >= 0;
  // 서버는 지수 조회에 실패하면 결정론 합성 시계열로 폴백한다(is_demo). 그걸 실시세처럼
  // 보여 주면 없는 숫자를 사실로 만든다 — 주가 차트(StockChart)와 같은 방식으로 밝힌다.
  const demo = idx.is_demo;
  const color = up ? KR_UP : KR_DOWN;
  return (
    <div
      className={`group flex items-center justify-between gap-2 rounded-[14px] px-3.5 py-3 transition ${
        tone === "glass"
          ? "glass hover-lift"
          : "border border-line/70 bg-white/55 hover:-translate-y-0.5 hover:border-navy/15 hover:shadow-[0_8px_22px_-10px_rgba(30,41,59,.28)]"
      } ${demo ? "opacity-60" : ""}`}
      title={demo ? "지수를 불러오지 못해 예시 값을 보여 줍니다(실데이터 아님)." : undefined}
    >
      <div className="min-w-0">
        <div className="flex items-center gap-1 text-[11.5px] font-semibold text-muted">
          <span>{idx.name}</span>
          {demo && (
            <span className="rounded-[4px] bg-muted/15 px-1 text-[10px] font-bold text-muted">예시</span>
          )}
        </div>
        <div className="mt-0.5 text-[17px] font-extrabold leading-tight text-navy">
          {idx.last.toLocaleString("ko-KR")}
        </div>
        <div
          className={`mt-0.5 inline-flex items-center gap-0.5 text-[11.5px] font-bold ${demo ? "text-muted" : ""}`}
          style={demo ? undefined : { color }}
        >
          {up ? "▲" : "▼"} {Math.abs(idx.change_pct)}%
        </div>
      </div>
      <Sparkline bars={idx.bars} up={up} idKey={idx.key} />
    </div>
  );
}

// "실시간" 배지가 실제로 갱신되도록 마운트 후 주기적으로 재조회한다. Yahoo 일봉 API 특성상
// 초 단위 실시간은 불가하므로 45초 폴링으로 최신 종가/현재가를 반영한다.
const REFRESH_MS = 45_000;

// variant "grid" = 리포트 상단(기존, 유리 카드 4열). "band" = 홈 대시보드 상단 가로 밴드(제목+실시간 배지 패널).
export function MarketIndices({ variant = "grid" }: { variant?: "grid" | "band" }) {
  const [items, setItems] = useState<MarketIndex[] | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      getMarketIndices()
        .then((d) => {
          if (alive) setItems(d.indices);
        })
        .catch(() => {
          // 일시적 실패 시 기존 값을 유지하고, 최초 로드 실패만 빈 상태로 표시한다.
          if (alive) setItems((prev) => prev ?? []);
        });
    void load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // 하나라도 예시 값이면 "실시간"이라고 말하지 않는다.
  const anyDemo = (items ?? []).some((idx) => idx.is_demo);

  if (variant === "band") {
    return (
      <section data-section="market-indices" className="glass-card p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-[14px] font-bold text-navy">시장 지수</h2>
          {anyDemo && (
            <span className="text-[11.5px] text-muted">일부 지수를 불러오지 못했습니다</span>
          )}
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

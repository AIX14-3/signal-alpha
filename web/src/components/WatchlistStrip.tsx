"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useWatchlistDayChange } from "@/hooks/useWatchlistDayChange";
import { useAuthStore } from "@/stores/authStore";
import { useWatchlistStore } from "@/stores/watchlistStore";

// 시세 등락 색은 한국 관례(상승=빨강 / 하락=파랑). 아코디언 캔들 차트와 동일 소스·색.
const KR_UP = "#ef4444";
const KR_DOWN = "#3b82f6";

// 관심종목 스트립 — 네비게이션 바로 아래에 내 관심종목을 칩으로 노출(전 페이지 공통).
// 로그인(관심종목 有) 시에만 표시. 각 칩은 해당 종목 리포트로 이동.
// 목록은 watchlistStore 공유 — 리포트의 관심종목 버튼 토글이 즉시 칩에 반영된다.
//
// 캐러셀: 가로 스크롤 + (1) 마우스 드래그-스크롤 (2) 좌우 화살표(끝에서 자동 숨김) (3) 가장자리 페이드.
// 터치/트랙패드는 네이티브 스크롤을 그대로 쓰고, 드래그-스크롤은 마우스에만 적용한다.
export function WatchlistStrip() {
  const status = useAuthStore((s) => s.status);
  const items = useWatchlistStore((s) => s.items);
  const ensureLoaded = useWatchlistStore((s) => s.ensureLoaded);
  const reset = useWatchlistStore((s) => s.reset);
  const changePcts = useWatchlistDayChange(items);

  const scrollerRef = useRef<HTMLDivElement>(null);
  const [canLeft, setCanLeft] = useState(false);
  const [canRight, setCanRight] = useState(false);
  // 드래그 상태(리렌더 불필요 → ref). moved 는 "드래그였는지"로, 칩 클릭이 리포트로 튀는 것을 막는다.
  const drag = useRef({ active: false, startX: 0, startScroll: 0, moved: false });

  useEffect(() => {
    if (status === "authenticated") void ensureLoaded();
    else if (status === "anonymous") reset();
  }, [status, ensureLoaded, reset]);

  const updateArrows = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    setCanLeft(el.scrollLeft > 4);
    setCanRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
  }, []);

  // 목록·창 크기 변화에 맞춰 화살표 노출을 갱신한다.
  // 등락률(%)이 비동기로 붙으며 칩 폭이 나중에 커지므로 ResizeObserver 로 콘텐츠 변화까지 잡는다.
  useEffect(() => {
    updateArrows();
    const el = scrollerRef.current;
    if (!el) return;
    el.addEventListener("scroll", updateArrows, { passive: true });
    window.addEventListener("resize", updateArrows);
    const ro = new ResizeObserver(updateArrows);
    ro.observe(el);
    for (const child of Array.from(el.children)) ro.observe(child);
    return () => {
      el.removeEventListener("scroll", updateArrows);
      window.removeEventListener("resize", updateArrows);
      ro.disconnect();
    };
  }, [items.length, changePcts, updateArrows]);

  const scrollByDir = useCallback((dir: 1 | -1) => {
    const el = scrollerRef.current;
    if (!el) return;
    el.scrollBy({ left: dir * Math.max(220, el.clientWidth * 0.7), behavior: "smooth" });
  }, []);

  // ── 마우스 드래그-스크롤 ── (터치/펜은 네이티브 스크롤에 맡긴다)
  const onPointerDown = (e: React.PointerEvent) => {
    if (e.pointerType !== "mouse") return;
    const el = scrollerRef.current;
    if (!el) return;
    drag.current = { active: true, startX: e.clientX, startScroll: el.scrollLeft, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const el = scrollerRef.current;
    if (!el || !drag.current.active) return;
    const dx = e.clientX - drag.current.startX;
    if (Math.abs(dx) > 4) drag.current.moved = true;
    el.scrollLeft = drag.current.startScroll - dx;
  };
  const endDrag = () => {
    drag.current.active = false;
  };
  // 드래그로 스크롤한 직후의 클릭은 삼킨다(칩을 옮기려다 리포트로 이동하는 오작동 방지).
  const onClickCapture = (e: React.MouseEvent) => {
    if (drag.current.moved) {
      e.preventDefault();
      e.stopPropagation();
      drag.current.moved = false;
    }
  };

  if (status !== "authenticated" || items.length === 0) return null;

  return (
    <div
      className="sticky top-16 z-40 border-b border-line bg-bg/60 backdrop-blur-md"
      data-section="watchlist-strip"
    >
      <div className="relative mx-auto max-w-[1320px]">
        {/* 좌 화살표 + 페이드 (스크롤 여지가 있을 때만) */}
        {canLeft && (
          <>
            <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-12 bg-gradient-to-r from-bg to-transparent" />
            <button
              type="button"
              aria-label="이전 관심종목"
              onClick={() => scrollByDir(-1)}
              className="absolute left-0 top-1/2 z-20 -translate-y-1/2 p-1 text-muted transition hover:text-navy"
            >
              <ChevronLeft size={20} strokeWidth={2.5} />
            </button>
          </>
        )}

        <div
          ref={scrollerRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerLeave={endDrag}
          onClickCapture={onClickCapture}
          className="flex select-none items-center gap-2 overflow-x-auto px-6 py-2 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden cursor-grab active:cursor-grabbing"
        >
          <span className="shrink-0 text-[12px] font-semibold text-muted">관심종목</span>
          {items.map((w) => {
            const change = changePcts[w.stock.stock_code];
            const up = change != null && change.pct >= 0;
            return (
              <Link
                key={w.stock.stock_code}
                href={`/report/${encodeURIComponent(w.stock.stock_code)}`}
                draggable={false}
                className="shrink-0 rounded-full border border-line px-3 py-1 text-[12.5px] font-medium text-navy-soft hover:border-navy hover:text-navy"
              >
                {w.stock.stock_name} <span className="text-muted">{w.stock.stock_code}</span>
                {change && (
                  <span className="ml-1.5 font-bold" style={{ color: up ? KR_UP : KR_DOWN }}>
                    {up ? "▲" : "▼"} {Math.abs(change.pct).toFixed(2)}%
                  </span>
                )}
              </Link>
            );
          })}
        </div>

        {/* 우 화살표 + 페이드 */}
        {canRight && (
          <>
            <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-12 bg-gradient-to-l from-bg to-transparent" />
            <button
              type="button"
              aria-label="다음 관심종목"
              onClick={() => scrollByDir(1)}
              className="absolute right-0 top-1/2 z-20 -translate-y-1/2 p-1 text-muted transition hover:text-navy"
            >
              <ChevronRight size={20} strokeWidth={2.5} />
            </button>
          </>
        )}
      </div>
    </div>
  );
}

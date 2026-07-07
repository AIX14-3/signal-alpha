"use client";

import { useHomeStore } from "@/stores/homeStore";

// FR-5 전역 뉴스 헤더 배너 — 페이지 최상단. recent_articles(최근 24h).
// NFR-2: track-record 문구만("분석한 시그널"), 예측/수익보장 표현 금지.
export function NewsSummaryBanner() {
  const summary = useHomeStore((s) => s.summary);
  const loading = useHomeStore((s) => s.loading);

  const count = summary?.recent_articles ?? 0;
  const stockCount = summary?.stock_count ?? 0;

  return (
    <section className="relative z-10 px-2 pb-6 pt-10 text-center sm:pt-14">
      <span className="pill mb-4 border border-line bg-surface-2 px-[13px] py-1.5 text-[13px] font-semibold text-navy-soft">
        <span className="live-dot" /> 멀티에이전트가 실시간으로 분석합니다
      </span>
      <h1 className="mx-auto max-w-[20ch] text-[clamp(26px,4.4vw,44px)] font-extrabold leading-[1.08] tracking-[-0.03em]">
        최근 24시간{" "}
        <span className="bg-gradient-to-r from-sky to-green bg-clip-text text-transparent">
          뉴스 {loading ? "…" : count.toLocaleString()}건
        </span>
        을 분석한 시그널
      </h1>
      <p className="mx-auto mt-3 max-w-[46ch] text-[15px] text-muted">
        공시·재무·뉴스·수급·시계열을 5개 에이전트가 분석해 종목별 판단 근거로 정리합니다
        {stockCount > 0 ? ` · 종목 ${stockCount.toLocaleString()}개` : ""}.
      </p>
    </section>
  );
}

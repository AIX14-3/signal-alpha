"use client";

import { useEffect } from "react";
import { BackgroundFX } from "@/components/BackgroundFX";
import { HomeLeftPane } from "@/components/HomeLeftPane";
import { HomeRightPane } from "@/components/HomeRightPane";
import { MarketIndices } from "@/components/MarketIndices";
import { NewsSummaryBanner } from "@/components/NewsSummaryBanner";
import { WatchlistSection } from "@/components/WatchlistSection";
import { useHomeStore } from "@/stores/homeStore";

export default function HomePage() {
  const init = useHomeStore((s) => s.init);
  useEffect(() => {
    void init();
  }, [init]);

  return (
    <>
      <BackgroundFX />
      <div className="dash-blobs" aria-hidden="true" />
      <NewsSummaryBanner />
      {/* 홈 대시보드 — 시장 지수·관심종목을 상단 가로 밴드로 올리고, 그 아래 2컬럼(좌 실시간 뉴스 피드 /
          우 실시간 분석 종목·커뮤니티 인기)을 배치한다. 데스크톱 2컬럼(1:2), 모바일 세로 스택(NFR-4). */}
      <div
        data-page="home"
        className="glass relative z-10 mb-16 flex flex-col gap-5 p-4 sm:p-5 lg:gap-6 lg:p-6"
      >
        <MarketIndices variant="band" />
        <WatchlistSection />
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_2fr] lg:gap-6">
          <HomeLeftPane />
          <HomeRightPane />
        </div>
      </div>
    </>
  );
}

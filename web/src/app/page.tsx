"use client";

import { useEffect } from "react";
import { BackgroundFX } from "@/components/BackgroundFX";
import { HomeLeftPane } from "@/components/HomeLeftPane";
import { HomeRightPane } from "@/components/HomeRightPane";
import { NewsSummaryBanner } from "@/components/NewsSummaryBanner";
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
      {/* 홈 v2 — 인-콘텐츠 메뉴 제거, 2컬럼: 좌 실시간 뉴스 피드 / 우 3섹션(관심·실시간 분석·커뮤니티 인기).
          데스크톱 2컬럼, 모바일 세로 스택(NFR-4). */}
      <div
        data-page="home"
        className="glass relative z-10 mb-16 grid grid-cols-1 gap-5 p-4 sm:p-5 lg:grid-cols-[1fr_380px] lg:gap-6 lg:p-6"
      >
        <HomeLeftPane />
        <HomeRightPane />
      </div>
    </>
  );
}
